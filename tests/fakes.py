"""A minimal stand-in for the Gmail API service object.

Tests monkeypatch ``gmail_client._service`` to return a ``FakeGmail`` so nothing
hits the network, no real credentials are loaded, and no token files are read.
The fake mirrors just the call chains the code under test actually uses, e.g.::

    service.users().messages().get(userId="me", id=..., format="full").execute()

Configure it with a ``responses`` dict keyed by ``"<collection>.<method>"``
(``"messages.get"``, ``"drafts.send"``, ``"users.getProfile"``, ...). A value
may be a plain response object or a callable taking the request kwargs and
returning one — use a callable to vary by id, or to ``raise`` and exercise
error paths. Every call is recorded in ``fake.calls`` for assertions.
"""


class _Request:
    def __init__(self, fake, key, kwargs):
        self._fake = fake
        self._key = key
        self._kwargs = kwargs

    def execute(self):
        resp = self._fake.responses.get(self._key)
        if callable(resp):
            return resp(self._kwargs)
        if resp is None:
            raise AssertionError(f"FakeGmail: no response configured for {self._key!r}")
        return resp


class _Collection:
    def __init__(self, fake, name):
        self._fake = fake
        self._name = name

    def _call(self, method, **kwargs):
        key = f"{self._name}.{method}"
        self._fake.calls.append((key, kwargs))
        return _Request(self._fake, key, kwargs)

    # The chain methods the client uses. Each just records and defers to responses.
    def list(self, **kw):
        return self._call("list", **kw)

    def get(self, **kw):
        return self._call("get", **kw)

    def create(self, **kw):
        return self._call("create", **kw)

    def update(self, **kw):
        return self._call("update", **kw)

    def patch(self, **kw):
        return self._call("patch", **kw)

    def send(self, **kw):
        return self._call("send", **kw)

    def modify(self, **kw):
        return self._call("modify", **kw)

    def trash(self, **kw):
        return self._call("trash", **kw)

    def untrash(self, **kw):
        return self._call("untrash", **kw)

    def delete(self, **kw):
        return self._call("delete", **kw)


class _Users:
    def __init__(self, fake):
        self._fake = fake

    def messages(self):
        return _Collection(self._fake, "messages")

    def drafts(self):
        return _Collection(self._fake, "drafts")

    def threads(self):
        return _Collection(self._fake, "threads")

    def labels(self):
        return _Collection(self._fake, "labels")

    def getProfile(self, **kw):
        self._fake.calls.append(("users.getProfile", kw))
        return _Request(self._fake, "users.getProfile", kw)


class FakeGmail:
    def __init__(self, responses=None):
        self.responses = dict(responses or {})
        self.calls = []

    def users(self):
        return _Users(self)


def message_with_headers(msg_id="m1", thread_id="t1", headers=None, snippet="", parts=None,
                         body_data=None, mime_type="text/plain", label_ids=None):
    """Build a message resource in roughly the shape the real API returns."""
    hdr_list = [{"name": k, "value": v} for k, v in (headers or {}).items()]
    payload = {"mimeType": mime_type, "headers": hdr_list, "body": {}}
    if body_data is not None:
        payload["body"] = {"data": body_data}
    if parts is not None:
        payload["parts"] = parts
    return {
        "id": msg_id,
        "threadId": thread_id,
        "snippet": snippet,
        "labelIds": label_ids or [],
        "payload": payload,
    }
