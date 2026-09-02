import { Archivo, Fraunces, IBM_Plex_Mono, Instrument_Serif } from "next/font/google";

/**
 * One font per design direction (see PreferencesPanel), self-hosted via
 * next/font so there is no runtime request to a font CDN and no layout shift.
 *
 * Each exposes a CSS variable rather than being applied directly -- the actual
 * `--font-display` / `--font-body` a page uses is chosen per `[data-design]`
 * in globals.css, not by which of these loaded.
 */

export const fraunces = Fraunces({
  subsets: ["latin"],
  weight: ["600", "900"],
  variable: "--font-fraunces",
  display: "swap",
});

export const instrumentSerif = Instrument_Serif({
  subsets: ["latin"],
  weight: "400",
  style: ["normal", "italic"],
  variable: "--font-instrument-serif",
  display: "swap",
});

export const archivo = Archivo({
  subsets: ["latin"],
  weight: ["500", "700", "900"],
  variable: "--font-archivo",
  display: "swap",
});

export const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-mono",
  display: "swap",
});

export const designFontVariables = [
  fraunces.variable,
  instrumentSerif.variable,
  archivo.variable,
  plexMono.variable,
].join(" ");
