"""Short-lived trusted access to the isolated shared lab mailbox for tests."""

from federated_cms import CMSCore


def mailbox_call(store, account, method, *args):
    service = CMSCore(store).lab_mailbox()
    session = service.open_session(account)
    try:
        return getattr(service, method)(session, *args)
    finally:
        service.close_session(session)
