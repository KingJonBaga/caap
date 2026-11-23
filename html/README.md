# html/ — Page Partials

These files are **reference partials** extracted from `index.html`.  
They exist so each page section can be found, read, and edited in isolation.

---

## Important

> **Do not open these files directly in a browser.**  
> The app runs as a single-page application. `index.html` at the project root is the only entry point. All CSS and JS are loaded there.

When you make a change in a partial file here, **copy the updated markup back into the matching section of `index.html`**.

---

## File map

| File | Page ID | Description |
|---|---|---|
| `login.html` | `#page-login` | Landing page, login form, nav, footer |
| `dashboard.html` | `#page-dashboard` | Patient dashboard — stats, vitals, appointments |
| `profile.html` | `#page-profile` | Patient profile — personal info, prenatal details |
| `records.html` | `#page-records` | Patient health records — visits, results |
| `appointments.html` | `#page-appointments` | Patient appointments — upcoming, past |
| `postpartum.html` | `#page-postpartum` | Postpartum care — checklist, wellbeing |
| `caretaker.html` | `#page-caretaker` | Caretaker workspace — tasks, patient view |
| `provider.html` | `#page-provider` | Care team workspace — overview, caseload, schedule |
| `admin.html` | `#page-admin` | Administrator workspace — accounts, audit log |

---

## Project structure

```
caap/
├── index.html           ← Entry point (do not remove)
│
├── html/                ← Page partials (this folder)
│   ├── README.md
│   ├── login.html
│   ├── dashboard.html
│   ├── profile.html
│   ├── records.html
│   ├── appointments.html
│   ├── postpartum.html
│   ├── caretaker.html
│   ├── provider.html
│   └── admin.html
│
├── css/
│   ├── tokens.css       ← CSS variables and global resets
│   ├── typography.css   ← Headings, nav brand, text styles
│   ├── login.css        ← Landing page layout and footer
│   ├── inputs.css       ← Form fields, checkboxes, password wrap
│   ├── buttons.css      ← Submit button, links, role buttons
│   ├── dashboard.css    ← All authenticated workspace styles
│   ├── provider.css     ← Care team overview panel (v2)
│   ├── caseload.css     ← Caseload panel (v2)
│   └── schedule.css     ← Provider schedule table
│
└── js/
    ├── breakpoints.js   ← Viewport class toggler (loads in head)
    ├── utils.js         ← Shared toast helpers
    ├── auth.js          ← Login, role detection, password toggle
    ├── navigation.js    ← Page routing, tabs, scrolling
    ├── admin.js         ← Admin panels, accounts, audit log
    ├── provider.js      ← Schedule, caseload, summaries, records
    └── init.js          ← App boot (runs last)
```
