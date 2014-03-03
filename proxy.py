#!/usr/bin/env python
# coding: utf-8
# http://musta.sh/2012-03-04/twisted-tcp-proxy.html

from os import environ

from twisted.internet import defer
from twisted.internet import protocol
from twisted.internet.protocol import Factory, Protocol
from twisted.internet import reactor

from twisted.web.static import File
from twisted.web.server import Site
from websockets import WebSocketsResource, lookupProtocolForFactory

from django.core import signing

import optparse

import logging
logger = logging.getLogger(__name__)

PROXY_SECRET = environ['PROXY_SECRET']
KEY_MAX_AGE = environ.get('KEY_MAX_AGE', 300)


class ProxyClientProtocol(protocol.Protocol):
    def connectionMade(self):
        self.dst = '%s:%d' % self.transport.addr
        logger.info("Client(%s): connected to qemu (%s)",
                    self.factory.src, self.dst)
        self.cli_queue = self.factory.cli_queue
        self.cli_queue.get().addCallback(self.serverDataReceived)

    def serverDataReceived(self, chunk):
        if chunk is False:
            self.cli_queue = None
            logger.info("Client(%s): disconnecting from qemu (%s)",
                        self.factory.src, self.dst)
            self.factory.continueTrying = False
            self.transport.loseConnection()
        elif self.cli_queue:
            logger.debug("Client(%s): writing %d bytes to qemu (%s)",
                         self.factory.src, len(chunk), self.dst)
            self.transport.write(chunk)
            self.cli_queue.get().addCallback(self.serverDataReceived)
        else:
            self.factory.cli_queue.put(chunk)

    def dataReceived(self, chunk):
        logger.debug("Client(%s): %d bytes received from qemu (%s)",
                     self.factory.src, len(chunk), self.dst)
        self.factory.srv_queue.put(chunk)

    def connectionLost(self, why):
        self.factory.srv_queue.put(False)
        if self.cli_queue:
            self.cli_queue = None
            logger.error("Client(%s): peer disconnected unexpectedly (%s)",
                         self.factory.src, self.dst)


class ProxyClientFactory(protocol.ReconnectingClientFactory):
    maxDelay = 10
    continueTrying = False
    protocol = ProxyClientProtocol

    def __init__(self, srv_queue, cli_queue, src):
        self.srv_queue = srv_queue
        self.cli_queue = cli_queue
        self.src = src

    def clientConnectionFailed(self, connector, reason):
        self.srv_queue.put(False)
        logger.error("Client(%s): unable to connect to qemu: %s",
                     self.src, reason)


class VNCWebSocketHandler(Protocol):
    def makeConnection(self, transport):
        try:
            value = signing.loads(transport.request.args['d'][0],
                                  key=PROXY_SECRET, max_age=KEY_MAX_AGE)
            try:
                self.src = transport.request.requestHeaders.getRawHeaders(
                    'x-real-ip')[0]
            except:
                self.src = str(transport.client)
            port = value['port']
            host = value['host']
        except Exception as e:
            src = getattr(self, 'src', None)
            logger.warning('Server(%s): bad connection,  key=%s err=%s',
                           src, transport.request.args['d'][0], e)
            transport.loseConnection()
            return
        logger.info("Server(%s): new connection, host=%s, port=%s",
                    self.src, host, port)
        self.transport = transport
        self.srv_queue = defer.DeferredQueue()
        self.cli_queue = defer.DeferredQueue()
        self.srv_queue.get().addCallback(self.clientDataReceived)

        factory = ProxyClientFactory(self.srv_queue, self.cli_queue, self.src)
        reactor.connectTCP(host, int(port), factory)

    def clientDataReceived(self, chunk):
        if chunk is False:
            self.transport.loseConnection()
        else:
            logger.debug("Server(%s): writing %d bytes to original client",
                         self.src, len(chunk))
            self.transport.write(chunk)
            self.srv_queue.get().addCallback(self.clientDataReceived)

    def dataReceived(self, frame):
        logger.debug("Server(%s): %d bytes received", self.src, len(frame))
        self.cli_queue.put(frame)

    def connectionLost(self, why):
        if hasattr(self, 'cli_queue'):
            self.cli_queue.put(False)
        logger.info("Server(%s): disconnected", self.src)


class VNCWebSocketFactory(Factory):
    protocol = VNCWebSocketHandler


if __name__ == "__main__":
    parser = optparse.OptionParser()
    parser.add_option("-I", "--loglevel", dest="loglevel", default='INFO',
                      help="loglevel")
    opts, args = parser.parse_args()
    logging.basicConfig(level=opts.loglevel)
    resource = File('.')
    resource.putChild('vnc', WebSocketsResource(
        lookupProtocolForFactory(VNCWebSocketFactory())))
    reactor.listenTCP(9999, Site(resource))
    reactor.run()
