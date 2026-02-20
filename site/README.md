# Kredo Astro Site

Marketing/docs frontend for `aikredo.com`, plus hosted browser app at `/app/`.

## Commands

Run from `site/`:

| Command | Action |
| :-- | :-- |
| `npm install` | Install dependencies |
| `npm run dev` | Start Astro dev server |
| `npm run sync:app` | Sync `../app` into `public/app` |
| `npm run build` | Sync app + build production `dist/` |
| `npm run preview` | Preview built site |
| `npm run deploy` | Build + push `dist/*` to production server |

## Web App Integration

The browser GUI source of truth is `../app/`.

Build pipeline behavior:
1. `npm run sync:app` mirrors `../app` to `site/public/app`
2. Astro copies `public/app` into final static output
3. Users access it at `https://aikredo.com/app/`
4. `https://app.aikredo.com` is configured as a 301 redirect to the canonical path above

Do not edit generated files inside `site/public/app` directly; edit `../app` instead.
