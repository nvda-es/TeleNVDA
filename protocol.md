# NVDA Remote Protocol Documentation

## Overview

The NVDA Remote protocol facilitates communication between two NVDA instances, enabling remote assistance and collaboration. It uses a client-server model where either client can act as the controlling (master) or controlled (slave) machine.

## Connection Establishment

1. Clients connect to a relay server using one of the supported transports (see below).
2. Clients authenticate by joining a shared channel.
3. The relay server facilitates message passing between connected clients.

## Transport Layer

The same JSON message protocol can be carried over two interchangeable transports. A single relay server can expose both at the same time (on separate ports), and clients on different transports interoperate transparently on the same channel.

### Raw TLS (default)

- A plain TCP connection wrapped in SSL/TLS (historically on port `6837`).
- Each JSON message is terminated with a newline character (`'\n'`).
- This is the original NVDA Remote transport and remains the default.

### WebSocket over HTTPS

- A `wss://` (TLS) WebSocket connection, typically served on port `443` so it can traverse restrictive corporate HTTP proxies.
- The client negotiates the `nvdaremote/2.0` WebSocket subprotocol during the HTTP upgrade handshake.
- Each JSON message is sent as a single WebSocket text frame. The newline terminator is optional and is trimmed by both ends if present.
- HTTP `CONNECT` proxies (with optional Basic authentication or system proxy settings) are supported for outbound connections.
- The application-layer AES-GCM encryption (`encrypted` messages) is applied on top of the WebSocket transport exactly as with raw TLS.

### Proxy support (WebSocket transport)

The WebSocket transport can reach the relay through several proxy types, selected by the `proxy_type` setting:

- `http` — HTTP `CONNECT` proxy (anonymous or Basic authentication).
- `socks4`, `socks4a`, `socks5`, `socks5h` — SOCKS proxies. The `a`/`h` variants perform DNS resolution on the proxy side.
- `negotiate`, `ntlm` — Windows Integrated Authentication proxies. The tunnel is authenticated with the credentials of the current Windows session via SSPI (single sign-on, no password stored).

The add-on offers three proxy modes in its Options dialog:

- **Manual configuration** (the default) preserves the historical behavior.
  Explicit HTTP/SOCKS fields are used when filled; otherwise the underlying
  network libraries may use their proxy environment variables.
- **Automatic Windows proxy detection** resolves the current destination with
  WinHTTP, including PAC/WPAD scripts and bypass rules, and uses the current
  Windows session for integrated authentication when required.
- **No proxy** forces a direct connection and ignores proxy environment
  variables.

#### TLS-inspecting proxies (SSL inspection)

Some corporate proxies terminate and re-encrypt TLS, presenting their own certificate instead of the relay's:

- If the proxy's certificate authority is trusted by the Windows certificate store (e.g. deployed by group policy), the connection is verified and established automatically, because certificate validation loads the Windows system certificate store.
- Otherwise the certificate is unverifiable. In that case the client retrieves the certificate fingerprint **through the proxy tunnel** (so it reflects the certificate actually presented on the wire), automatically trusts it, and remembers it for subsequent connections so automatic connections are not blocked by a prompt. The expected fingerprint should be verified with the relay administrator before the first connection.

## Message Format

Messages are serialized as JSON objects with a 'type' field indicating the message type. Over raw TLS each message is terminated with a newline character (`'\n'`); over WebSocket each message is one text frame.

## Protocol Version Negotiation

1. Upon connection, the client sends a `protocol_version` message.
2. If versions are incompatible, an error is sent and the connection is closed.

## Capability Negotiation

The `protocol_version` message is handled by the relay and says nothing about the
optional features implemented by the other clients. Because the relay forwards
every message it does not understand, and because clients ignore message types
they do not know, clients announce their own optional features with a
`telenvda_capabilities` message.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "definitions": {
    "telenvda_capabilities": {
      "type": "object",
      "properties": {
        "type": { "const": "telenvda_capabilities" },
        "negotiation_version": { "type": "integer", "description": "Version of the negotiation format itself." },
        "addon": { "type": "string", "description": "Name of the add-on, e.g. 'TeleNVDA'." },
        "addon_version": { "type": "string", "description": "Version of the add-on." },
        "features": { "type": "array", "items": { "type": "string" }, "description": "Optional features implemented by the sender, e.g. 'chunked_file_transfer'." },
        "max_file_size": { "type": ["integer", "null"], "description": "Largest file the sender accepts to receive, or null when only limited by the available disk space." },
        "reply": { "type": "boolean", "description": "When true, the receiver answers with its own announcement." }
      },
      "required": ["type", "features"]
    }
  }
}
```

The message is broadcast when a client joins a channel and every time another
client joins, so that both ends learn about each other whatever their join
order. A client which never answers is considered a legacy client, and every
optional feature falls back to the behaviour understood by the original add-on.

## Message Types

Below is a detailed specification of each message type using JSONSchema:

### Connection Setup

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "definitions": {
    "protocol_version": {
      "type": "object",
      "properties": {
        "type": { "const": "protocol_version" },
        "version": { "type": "integer" }
      },
      "required": ["type", "version"]
    },
    "join": {
      "type": "object",
      "properties": {
        "type": { "const": "join" },
        "channel": { "type": "string" },
        "connection_type": { "enum": ["master", "slave"] }
      },
      "required": ["type", "channel", "connection_type"]
    },
    "channel_joined": {
      "type": "object",
      "properties": {
        "type": { "const": "channel_joined" },
        "channel": { "type": "string" },
        "clients": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "id": { "type": "integer" },
              "connection_type": { "enum": ["master", "slave"] }
            },
            "required": ["id", "connection_type"]
          }
        }
      },
      "required": ["type", "channel", "clients"]
    },
    "client_joined": {
      "type": "object",
      "properties": {
        "type": { "const": "client_joined" },
        "client": {
          "type": "object",
          "properties": {
            "id": { "type": "integer" },
            "connection_type": { "enum": ["master", "slave"] }
          },
          "required": ["id", "connection_type"]
        }
      },
      "required": ["type", "client"]
    },
    "client_left": {
      "type": "object",
      "properties": {
        "type": { "const": "client_left" },
        "client": { "type": "integer" }
      },
      "required": ["type", "client"]
    }
  }
}
```

### Control Messages

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "definitions": {
    "request_local_control": {
      "type": "object",
      "properties": {
        "type": { "const": "request_local_control" }
      },
      "required": ["type"]
    },
    "key": {
      "type": "object",
      "properties": {
        "type": { "const": "key" },
        "vk_code": { "type": "integer" },
        "scan_code": { "type": "integer" },
        "extended": { "type": "boolean" },
        "pressed": { "type": "boolean" }
      },
      "required": ["type", "vk_code", "scan_code", "extended", "pressed"]
    },
    "mouse": {
      "type": "object",
      "properties": {
        "type": { "const": "mouse" },
        "t": { "enum": ["m", "md", "mu", "w"] },
        "x": { "type": "number", "minimum": 0, "maximum": 1 },
        "y": { "type": "number", "minimum": 0, "maximum": 1 },
        "b": { "enum": ["left", "right", "middle"] },
        "d": { "type": "integer" },
        "h": { "type": "boolean" }
      },
      "required": ["type", "t"]
    },
    "speak": {
      "type": "object",
      "properties": {
        "type": { "const": "speak" },
        "sequence": {
          "type": "array",
          "items": {
            "oneOf": [
              { "type": "string" },
              {
                "type": "array",
                "items": [
                  { "type": "string" },
                  { "type": "object" }
                ],
                "minItems": 2,
                "maxItems": 2
              }
            ]
          }
        },
        "priority": { "type": "string" }
      },
      "required": ["type", "sequence", "priority"]
    },
    "cancel": {
      "type": "object",
      "properties": {
        "type": { "const": "cancel" }
      },
      "required": ["type"]
    },
    "pause_speech": {
      "type": "object",
      "properties": {
        "type": { "const": "pause_speech" },
        "switch": { "type": "boolean" }
      },
      "required": ["type", "switch"]
    },
    "tone": {
      "type": "object",
      "properties": {
        "type": { "const": "tone" },
        "hz": { "type": "number" },
        "length": { "type": "number" },
        "left": { "type": "number" },
        "right": { "type": "number" }
      },
      "required": ["type", "hz", "length", "left", "right"]
    },
    "wave": {
      "type": "object",
      "properties": {
        "type": { "const": "wave" },
        "fileName": { "type": "string" },
        "asynchronous": { "type": "boolean" }
      },
      "required": ["type", "fileName"]
    },
    "display": {
      "type": "object",
      "properties": {
        "type": { "const": "display" },
        "cells": { "type": "array", "items": { "type": "integer" } }
      },
      "required": ["type", "cells"]
    },
    "braille_input": {
      "type": "object",
      "properties": {
        "type": { "const": "braille_input" },
        "dots": { "type": "integer" },
        "space": { "type": "boolean" },
        "routingIndex": { "type": "integer" }
      },
      "required": ["type"]
    },
    "set_clipboard_text": {
      "type": "object",
      "properties": {
        "type": { "const": "set_clipboard_text" },
        "text": { "type": "string" }
      },
      "required": ["type", "text"]
    },
    "send_SAS": {
      "type": "object",
      "properties": {
        "type": { "const": "send_SAS" }
      },
      "required": ["type"]
    },
    "request_screenshot": {
      "type": "object",
      "properties": {
        "type": { "const": "request_screenshot" },
        "method": { "type": "string", "description": "Capture method: 'native' (default) or 'powershell'. Ignored by versions that only implement the native capture." }
      },
      "required": ["type"]
    },
    "screenshot": {
      "type": "object",
      "properties": {
        "type": { "const": "screenshot" },
        "content": { "type": "string", "description": "Base64-encoded image data of the controlled machine's screen." },
        "format": { "type": "string", "description": "Image format of the encoded content, e.g. 'jpg'." }
      },
      "required": ["type", "content"]
    }
  }
}
```

The screenshot feature always transfers an image from the controlled (slave)
machine to the controlling (master) machine, but it can be triggered from either
end:

- The controlling machine sends `request_screenshot`. The controlled machine
  captures its screen, scales it down to a lower (but still readable) resolution,
  and returns it as a `screenshot` message. The optional `method` field selects
  the capture method; a controlled machine that does not implement the requested
  method falls back to its native capture.
- The controlled machine can also push its screen directly by sending a
  `screenshot` message without a preceding request.

Upon receiving a `screenshot` message, the controlling machine writes the
decoded image to a temporary file and opens it with the system's default image
viewer.

#### Remote mouse

The `mouse` message lets the controlling machine drive the pointer of the
controlled machine. It always travels from master to slave; a slave never emits
it. Relays forward it like any other message, so no server support is needed.

The `t` field says what happened: `m` for a move, `md` and `mu` for a button
press and release, `w` for the wheel. Buttons are named in `b`, wheel notches in
`d` (positive scrolls away from the user) with `h` set for a horizontal wheel.
Button and wheel events may carry a position too, so that a click lands where the
user aimed even if the preceding move was dropped.

`x` and `y` are **fractions of the virtual desktop**, between 0 and 1, never
pixels. The two machines rarely share a resolution, a scaling factor or a monitor
layout, so a pixel position would land somewhere else on the other side. The
controlling machine samples its pointer about twenty times per second and skips
movements too small to matter, rather than sending every operating system event.

The controlled machine applies these events with `SendInput` only when its own
configuration allows remote input, and after its user has agreed if confirmation
is required. The agreement is forgotten when the connection ends, so reconnecting
never silently reuses an old answer. Injected events are tagged so that the local
mouse hook ignores them, which prevents a machine acting as both ends from
echoing events back and forth.

This feature is useful even without screen sharing: the screen reader of the
controlled machine announces whatever the pointer lands on, and that speech
returns over the connection which is already open.

### File Transfer

Two formats coexist.

The legacy `file_transfer` message carries the whole file Base64-encoded in a
single message. It is the only format understood by the original TeleNVDA, whose
sender refuses files larger than 10 MB. It is used whenever the peer did not
announce the `chunked_file_transfer` feature.

When the peer announced `chunked_file_transfer`, and when exactly one other
client is connected to the channel, a streaming transfer made of ordered
messages is used instead:

1. `file_transfer_start` announces a transfer identifier, the file name, the
  total size and the chunk size.
2. The receiver answers with `file_transfer_ack` for the `start` stage, with
  `accepted` set to `true` once the user chose where the file is saved, or to
  `false` with a `reason` when the transfer is declined.
3. `file_transfer_chunk` carries one Base64-encoded chunk and its index.
  Chunks are acknowledged individually with the `chunk` stage. The sender keeps
  a small number of chunks in flight, so memory use stays bounded and a stalled
  receiver applies backpressure to the sender.
4. `file_transfer_complete` carries the total size and the SHA-256 checksum of
  the whole file, and is acknowledged with the `complete` stage.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "definitions": {
    "file_transfer_start": {
      "type": "object",
      "properties": {
        "type": { "const": "file_transfer_start" },
        "id": { "type": "string" },
        "name": { "type": "string" },
        "size": { "type": "integer" },
        "chunk_size": { "type": "integer" }
      },
      "required": ["type", "id", "name", "size"]
    },
    "file_transfer_chunk": {
      "type": "object",
      "properties": {
        "type": { "const": "file_transfer_chunk" },
        "id": { "type": "string" },
        "index": { "type": "integer" },
        "data": { "type": "string" }
      },
      "required": ["type", "id", "index", "data"]
    },
    "file_transfer_complete": {
      "type": "object",
      "properties": {
        "type": { "const": "file_transfer_complete" },
        "id": { "type": "string" },
        "size": { "type": "integer" },
        "checksum": { "type": "string", "description": "Lowercase hexadecimal SHA-256 of the whole file." }
      },
      "required": ["type", "id", "size", "checksum"]
    },
    "file_transfer_ack": {
      "type": "object",
      "properties": {
        "type": { "const": "file_transfer_ack" },
        "id": { "type": "string" },
        "stage": { "enum": ["start", "chunk", "complete"] },
        "index": { "type": "integer", "description": "Index of the acknowledged chunk, for the 'chunk' stage." },
        "accepted": { "type": "boolean", "description": "Whether the transfer is accepted, for the 'start' stage." },
        "reason": { "type": "string" }
      },
      "required": ["type", "id", "stage"]
    },
    "file_transfer_abort": {
      "type": "object",
      "properties": {
        "type": { "const": "file_transfer_abort" },
        "id": { "type": "string" },
        "reason": { "type": "string" }
      },
      "required": ["type", "id"]
    }
  }
}
```

An interrupted transfer is announced with `file_transfer_abort` by either end.
The receiver writes incoming data to a temporary file next to the destination
and moves it to the selected path only after the size and checksum have been
verified. There is no fixed maximum size for the chunked format; the practical
limits are the available disk space, the limit announced by the receiver in its
capabilities, network reliability and transfer timeouts.

Transfers work in both directions: the controlling and the controlled computer
use the same code and either of them may start a transfer.

### Screen Sharing

The controlling computer may display the screen of the controlled one over a
peer to peer WebRTC link. The pictures never travel through the relay: it only
carries the few messages needed to set the link up, and the two computers then
talk to each other directly, falling back on a TURN server when the network
leaves them no other route.

This feature is optional at every level. A relay built without it, or started
without `-screen-share`, simply forwards the messages like any other, and the
clients then fail to establish anything. A client which does not announce
`screen_share` in its `telenvda_capabilities` is never asked to share anything.

#### Relay capabilities

Unlike `telenvda_capabilities`, which clients exchange between themselves, the
`capabilities` message is read by the relay and tells it which signalling it may
route on behalf of that client.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "definitions": {
    "capabilities": {
      "type": "object",
      "properties": {
        "type": { "const": "capabilities" },
        "capabilities": {
          "type": "array",
          "items": { "enum": ["screen_share/1", "input_control/1"] },
          "description": "Versioned capability strings. The relay ignores anything it does not know about."
        }
      },
      "required": ["type", "capabilities"]
    }
  }
}
```

#### Unicast routing

Every message listed below is delivered to a single client rather than broadcast
to the channel, which is what makes screen sharing safe on a channel holding
several controlling computers. The sender names the recipient in `target`, and
the relay stamps `origin` with the identifier of the sender before delivering
the message. A client must consider `origin` authoritative and ignore any
identifier found inside the payload, since only the relay can set it.

The relay refuses to route a message when either end lacks the `screen_share/1`
capability, when a controlling computer has not been authorized on the channel,
or when the sender exceeds 250 signalling messages over ten seconds. Failures
are reported to the sender with an ordinary `error` message whose `error` field
holds `screen_share_unsupported`, `not_authorized`, `invalid_parameters`,
`target_not_found` or `turn_unavailable`. A missing target is reported the same
way as an unauthorized one, so that the identifiers present on a relay cannot be
discovered by trying them.

#### Encryption

When the session is encrypted, these messages are not wrapped in the usual
`encrypted` envelope, because the relay has to read `type` and `target` to
deliver them. Instead the routing fields stay readable and everything else is
sealed in an `enc` object holding the base64 encoded `nonce`, `data` and `tag`
of an AES-GCM operation keyed with the session password. A message arriving
without a readable `enc` object was not produced by a peer holding that
password, and is dropped.

#### ICE servers

A client asks the relay for the servers it should use with a `turn_credentials`
message carrying nothing else. The relay answers with the same type. TURN
credentials are derived from a secret the clients never see and expire after
`ttl` seconds, so they are requested again for every session.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "definitions": {
    "turn_credentials": {
      "type": "object",
      "properties": {
        "type": { "const": "turn_credentials" },
        "ice_servers": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "urls": { "type": "array", "items": { "type": "string" } },
              "username": { "type": "string", "description": "Present on TURN entries only." },
              "credential": { "type": "string", "description": "Present on TURN entries only." }
            },
            "required": ["urls"]
          }
        },
        "ttl": { "type": "integer", "description": "Lifetime of the credentials, in seconds." }
      },
      "required": ["type"]
    }
  }
}
```

#### Session setup

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "definitions": {
    "screen_share_request": {
      "type": "object",
      "properties": {
        "type": { "const": "screen_share_request" },
        "target": { "type": "integer" },
        "origin": { "type": "integer" },
        "allow_input": { "type": "boolean", "description": "Whether the sender is willing to drive the mouse. The receiver still decides on its own." }
      },
      "required": ["type", "target"]
    },
    "screen_share_response": {
      "type": "object",
      "properties": {
        "type": { "const": "screen_share_response" },
        "target": { "type": "integer" },
        "origin": { "type": "integer" },
        "accepted": { "type": "boolean" },
        "allow_input": { "type": "boolean", "description": "Whether mouse control was actually granted." },
        "reason": { "enum": ["declined", "busy", "unavailable"], "description": "Present when the request was refused." }
      },
      "required": ["type", "target", "accepted"]
    },
    "screen_share_stop": {
      "type": "object",
      "properties": {
        "type": { "const": "screen_share_stop" },
        "target": { "type": "integer" },
        "origin": { "type": "integer" }
      },
      "required": ["type", "target"]
    },
    "webrtc_offer": {
      "type": "object",
      "properties": {
        "type": { "const": "webrtc_offer" },
        "target": { "type": "integer" },
        "origin": { "type": "integer" },
        "sdp": { "type": "string" }
      },
      "required": ["type", "target", "sdp"]
    },
    "webrtc_answer": {
      "type": "object",
      "properties": {
        "type": { "const": "webrtc_answer" },
        "target": { "type": "integer" },
        "origin": { "type": "integer" },
        "sdp": { "type": "string" }
      },
      "required": ["type", "target", "sdp"]
    },
    "webrtc_candidate": {
      "type": "object",
      "properties": {
        "type": { "const": "webrtc_candidate" },
        "target": { "type": "integer" },
        "origin": { "type": "integer" },
        "candidate": { "type": "string", "description": "A JSON encoded RTCIceCandidateInit, carried as a string." }
      },
      "required": ["type", "target", "candidate"]
    }
  }
}
```

A session runs as follows. The controlling computer sends
`screen_share_request`. The controlled one asks its user, unless that
confirmation was turned off, and answers with `screen_share_response`. On
acceptance the controlled computer opens the WebRTC session, so it is the one
sending `webrtc_offer`; the controlling computer replies with `webrtc_answer`.
Both ends send `webrtc_candidate` as routes are discovered, without waiting for
the description exchange to complete. Either end may send `screen_share_stop`,
and a session ends by itself when the relay connection drops.

`allow_input` never grants anything on its own. The controlling computer states
what it would like, but the controlled computer only ever grants what its own
configuration allows, and the answer says what was really granted. Only mouse
actions can be replayed this way: no keyboard input travels over this link.

The pictures themselves are carried on a WebRTC data channel rather than a media
track, as still frames compressed to JPEG and split into chunks. That format is
private to the two helper programs and is not part of this protocol.

### Braille Support

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "definitions": {
    "set_braille_info": {
      "type": "object",
      "properties": {
        "type": { "const": "set_braille_info" },
        "name": { "type": "string" },
        "numCells": { "type": "integer" }
      },
      "required": ["type", "name", "numCells"]
    },
    "set_display_size": {
      "type": "object",
      "properties": {
        "type": { "const": "set_display_size" },
        "sizes": { "type": "array", "items": { "type": "integer" } }
      },
      "required": ["type", "sizes"]
    }
  }
}
```

### Miscellaneous

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "definitions": {
    "ping": {
      "type": "object",
      "properties": {
        "type": { "const": "ping" }
      },
      "required": ["type"]
    },
    "error": {
      "type": "object",
      "properties": {
        "type": { "const": "error" },
        "message": { "type": "string" }
      },
      "required": ["type", "message"]
    }
  }
}
```

## Security Considerations

- All connections are encrypted using SSL/TLS.
- Clients can verify the server's certificate fingerprint to prevent man-in-the-middle attacks.
- The channel key acts as a shared secret for authentication.
- Screen sharing signalling is delivered to a single client, chosen by the relay
  from the `target` field, and stamped with an `origin` a client cannot forge.
  Its payload is sealed separately so that the relay routes the message without
  reading what it carries. Sharing a screen and granting mouse control are two
  distinct decisions, both taken on the computer being shared.

## Error Handling

- Connection errors trigger automatic reconnection attempts.
- Protocol errors are communicated using the `error` message type.

This protocol documentation provides a high-level overview of the NVDA Remote functionality. For detailed implementation, refer to the source code files, particularly `transport.py`, `session.py`, and `serializer.py`.
