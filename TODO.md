# TODO

Forward-looking only. When something ships, delete its line; git history is
the record.

- [ ] The per-model usage poll (`claude -p /usage`, every 10 minutes per
      account) is one more OAuth token user per account. If logouts return
      on a machine with several concurrent sessions, make the poll opt-in
      or slower.
- [ ] A terminal other than iTerm2 (Ghostty, Terminal.app): the table works;
      send, open and close do not. Worth a second backend if someone asks.
- [ ] `podbay send` loses everything before the last 1024 bytes of a long
      message: the text goes in through the terminal line discipline, which
      cuts a line at MAX_CANON. Seen 2026-10-04 with an 1132-byte message,
      of which only the tail reached the session. `podbay open` already hands
      the first prompt over in a file; send needs the same, or a split into
      lines under the limit.
