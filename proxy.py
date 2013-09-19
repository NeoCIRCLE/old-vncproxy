#!/usr/bin/env python
# coding: utf-8
# http://musta.sh/2012-03-04/twisted-tcp-proxy.html

import sys

from twisted.internet import defer
from twisted.internet import protocol
from twisted.internet.protocol import Factory, Protocol
from twisted.internet import reactor
from twisted.python import log

from twisted.web.static import File
from twisted.web.server import Site
from twisted.web.websockets import WebSocketsResource, lookupProtocolForFactory

from django.core import signing


class ProxyClientProtocol(protocol.Protocol):
    def connectionMade(self):
        log.msg("Client: connected to peer")
        self.cli_queue = self.factory.cli_queue
        self.cli_queue.get().addCallback(self.serverDataReceived)

    def serverDataReceived(self, chunk):
        if chunk is False:
            self.cli_queue = None
            log.msg("Client: disconnecting from peer")
            self.factory.continueTrying = False
            self.transport.loseConnection()
        elif self.cli_queue:
            log.msg("Client: writing %d bytes to peer" % len(chunk))
            self.transport.write(chunk)
            self.cli_queue.get().addCallback(self.serverDataReceived)
        else:
            self.factory.cli_queue.put(chunk)

    def dataReceived(self, chunk):
        log.msg("Client: %d bytes received from peer" % len(chunk))
        self.factory.srv_queue.put(chunk)

    def connectionLost(self, why):
        self.factory.srv_queue.put(False)
        if self.cli_queue:
            self.cli_queue = None
            log.msg("Client: peer disconnected unexpectedly")


class ProxyClientFactory(protocol.ReconnectingClientFactory):
    maxDelay = 10
    continueTrying = False
    protocol = ProxyClientProtocol

    def __init__(self, srv_queue, cli_queue):
        self.srv_queue = srv_queue
        self.cli_queue = cli_queue


class VNCWebSocketHandler(Protocol):
    def makeConnection(self, transport):
        try:
            value = signing.loads(transport.request.args['d'][0],
                                  key='asdasd', max_age=300)
            port = value['port']
            host = value['host']
        except:
            pass
        self.transport = transport
        self.srv_queue = defer.DeferredQueue()
        self.cli_queue = defer.DeferredQueue()
        self.srv_queue.get().addCallback(self.clientDataReceived)

        factory = ProxyClientFactory(self.srv_queue, self.cli_queue)
        reactor.connectTCP(host, int(port), factory)

    def clientDataReceived(self, chunk):
        if chunk is False:
            self.transport.loseConnection()
        else:
            log.msg("Server: writing %d bytes to original client" % len(chunk))
            self.transport.write(chunk)
            self.srv_queue.get().addCallback(self.clientDataReceived)

    def dataReceived(self, frame):
        log.msg("Server: %d bytes received" % len(frame))
        self.cli_queue.put(frame)

    def connectionLost(self, why):
        self.cli_queue.put(False)
        log.msg("HELO")


class VNCWebSocketFactory(Factory):
    protocol = VNCWebSocketHandler


if __name__ == "__main__":
    log.startLogging(sys.stdout)
    resource = File('.')
    resource.putChild('vnc', WebSocketsResource(
            lookupProtocolForFactory(VNCWebSocketFactory())))
    reactor.listenTCP(9999, Site(resource))
    reactor.run()
