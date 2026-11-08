// The first-party tracker's settings (jeeves practices/web-tracking.md).
// Hash routes count as page views. sanitize runs over the path and every
// event value: an address and long digit runs are removed and every value
// is cut to 160 characters. The app never passes message text or a board
// line to the tracker; this is the second guard.
window.TRACKER_CONFIG = {
  spa: true,
  sanitize: function (v) {
    return String(v)
      .replace(/[^\s@]+@[^\s@]+/g, "@")
      .replace(/\d{5,}/g, "#")
      .slice(0, 160);
  },
};
