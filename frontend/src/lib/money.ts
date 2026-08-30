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
  return FORMATTER.format(minor / 100);
}

/** Signed display: an explicit + on positive drivers reads better in a breakdown. */
export function formatSignedMinor(minor: Minor): string {
  const formatted = formatMinor(Math.abs(minor));
  if (minor < 0) return `−${formatted}`;
  return `+${formatted}`;
}

export function isNegative(minor: Minor): boolean {
  return minor < 0;
}
