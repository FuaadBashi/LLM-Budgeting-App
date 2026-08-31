/**
 * Money crosses the API boundary as integer minor units (pence).
 *
 * JavaScript numbers are IEEE-754 doubles, so a decimal amount is already
 * approximate by the time it is parsed. Integers up to 2^53 are exact, which
 * covers any personal balance, so arithmetic stays in minor units and formatting
 * happens only at the point of display.
 */

export type Minor = number;

const FORMATTER = new Intl.NumberFormat("en-GB", {
  style: "currency",
  currency: "GBP",
});

export function formatMinor(minor: Minor): string {
  // Intl emits a hyphen-minus; formatSignedMinor uses a true minus (U+2212).
  // Both appear inside the same card, so normalise on the typographic one.
  return FORMATTER.format(minor / 100).replace("-", "−");
}

/**
 * Signed display for breakdown rows: an explicit + marks a positive driver.
 *
 * Zero takes no sign. "+£0.00" reads as a positive contribution of nothing,
 * which is a distraction in a column the eye scans for direction.
 */
export function formatSignedMinor(minor: Minor): string {
  const formatted = formatMinor(Math.abs(minor));
  if (minor < 0) return `−${formatted}`;
  if (minor > 0) return `+${formatted}`;
  return formatted;
}

export function isNegative(minor: Minor): boolean {
  return minor < 0;
}

/** Parse a user-entered GBP amount without routing money through a float. */
export function parseMajorToMinor(value: string): Minor | null {
  const match = value.trim().match(/^\+?(\d+)(?:\.(\d{1,2}))?$/);
  if (!match) return null;

  const pounds = Number(match[1]);
  const pence = Number((match[2] ?? "").padEnd(2, "0"));
  const minor = pounds * 100 + pence;
  return Number.isSafeInteger(minor) ? minor : null;
}
