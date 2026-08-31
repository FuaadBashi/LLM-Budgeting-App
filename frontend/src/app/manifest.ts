import type { MetadataRoute } from "next";

/**
 * Installability, so the app can live on a home screen.
 *
 * The reason this is worth having is not the icon: it is that receipt capture
 * wants a camera, and a browser tab is a poor place to reach for one. `display:
 * standalone` also drops the URL bar, which is the difference between something
 * that feels like a tool and something that feels like a bookmark.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Personal Finance OS",
    short_name: "Finance OS",
    description: "Ledger-first personal finance",
    start_url: "/",
    display: "standalone",
    background_color: "#1a1a19",
    theme_color: "#1a1a19",
    orientation: "portrait",
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
