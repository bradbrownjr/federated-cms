# Deferred cleanup

Intentional technical debt: temporary implementations and limits kept on
purpose. Each entry says why it exists and when it can go.

## KISS lab bridge refuses extended (modulo-128) AX.25 setup

- **What:** `ScopedKiss.dispatch` in `src/federated_cms/kiss_lab.py` answers
  SABME with DM, so clients fall back to SABM (modulo 8).
- **Why it still exists:** the TCP KISS path decodes I and S frames as
  modulo 8 only. Accepting SABME would mis-read sequence numbers.
- **Remove when:** kissterm's frame decoder handles modulo-128 I and S frames
  and the bridge passes a modulo-128 session test.
- **Added:** 2026-11-25, PR #15.
