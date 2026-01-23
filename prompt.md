# Project: FPC Corinth Digital Bulletin

## Overview
Build a mobile-friendly digital bulletin website for First Presbyterian Church of Corinth, MS. The site will be hosted at `bulletin.fpccorinth.org` and serve as a companion to the printed Sunday bulletin—making it easy to follow along on a phone during worship.

## Tech Stack
- **Hosting:** Netlify
- **CMS:** Decap CMS (formerly Netlify CMS) or similar headless CMS for easy weekly updates
- **Framework:** Keep it simple—static site generator (11ty, Astro, or even plain HTML/CSS/JS if simpler)
- **Styling:** Tailwind CSS or minimal custom CSS—mobile-first, clean, readable

## Design Requirements

### Branding
- **Primary color:** Navy blue `#1a3a5c`
- **Accent color:** Gold `#c9a959`
- **Background:** Warm cream/off-white `#faf9f7` or white
- **Typography:** Serif for headings (Georgia or similar), clean sans-serif for body
- **Logo:** Church ichthus logo with "To Be Disciples / To Make Disciples"
- **Denomination:** EPC (Evangelical Presbyterian Church)

### Mobile-First Priorities
- Large, readable text (16px minimum body, larger for headings)
- Generous line height and spacing
- No horizontal scrolling
- Thumb-friendly tap targets
- Fast loading—minimal JavaScript
- Works offline if possible (service worker)

### Dark Mode
- Optional toggle for those who prefer less brightness during service
- Should respect system preference by default

## Content Structure

Each weekly bulletin should include these sections (based on actual bulletin structure):

### 1. Header
- Date
- Liturgical season/Sunday name (e.g., "Third Sunday after Epiphany")
- Optional: sermon series title

### 2. Order of Worship
Structured, collapsible or scrollable sections:
- Prelude
- Call to Meditation (scripture)
- Choral Introit
- Call to Worship (responsive reading—format Leader/All clearly)
- Hymns (number and title—consider linking to Hymnary.org)
- Responsive Readings
- Profession of Faith
- Prayers (Invocation, Confession, Lord's Prayer)
- Assurance of Pardon
- Gloria Patri (lyrics)
- Offering
- Doxology (lyrics)
- Choral Anthem (title, lyrics if available)
- Scripture Reading
- Sermon (title, speaker)
- Closing Hymn
- Benediction
- Postlude

### 3. Announcements Section
- Flowers dedication
- Upcoming events
- General announcements

### 4. Optional Extras
- Full anthem lyrics (expandable)
- Links to scripture passages (Bible Gateway or ESV.org)
- Prayer requests (if appropriate for public)

## Pages/Routes

```
/                   → Current week's bulletin (or redirect to latest)
/2026-01-25         → Specific date's bulletin
/archive            → List of past bulletins by date
/about              → Brief info about FPC Corinth, service times, address
```

## CMS Requirements

Staff (non-technical) should be able to:
1. Create a new bulletin each week by filling in fields
2. Copy from previous week as a starting template
3. Add/remove announcement blocks
4. Preview before publishing
5. Schedule publish date if possible

### Suggested CMS Fields
```yaml
date: 2026-01-25
liturgical_day: "Third Sunday after Epiphany"
sermon_title: "The Glue that Holds God's People Together"
sermon_speaker: "Bobby Capps, Dir. of Outreach & Care"
sermon_scripture: "Luke 6:27-36"

call_to_meditation:
  reference: "Jeremiah 31:2,3"
  text: "Thus says the Lord…I have loved you with an everlasting love..."

call_to_worship:
  reference: "Psalm 34:1-3"
  lines:
    - leader: "I will bless the Lord at all times;"
      all: "his praise shall continually be in my mouth."
    # ... etc

hymns:
  - title: "The Love of God"
    number: 67
    verses: "1,2"
  # ... etc

announcements:
  - title: "Senior Adult Valentine Banquet"
    body: "Monday, February 9th at 6:00 PM..."
    
flowers:
  given_by: "Brandon Lowrey"
  in_memory_of: "John Burnett on his birthday"
```

## Features to Include

### Must Have
- [ ] Mobile-responsive layout
- [ ] Current bulletin loads by default
- [ ] Clear visual hierarchy for order of worship
- [ ] Leader/All formatting for responsive readings
- [ ] CMS for easy updates
- [ ] Basic archive/history

### Nice to Have
- [ ] Dark mode toggle
- [ ] "Follow along" mode with larger text
- [ ] QR code generator for printed bulletins
- [ ] Scripture links to Bible Gateway
- [ ] Share button (native mobile share)
- [ ] Offline support via service worker
- [ ] Print-friendly stylesheet

### Future Considerations
- Push notifications for bulletin availability
- Integration with church calendar
- Audio/video sermon links
- Song lyrics with CCLI compliance

## File Structure Suggestion

```
/
├── src/
│   ├── _includes/
│   │   └── layouts/
│   │       └── bulletin.njk
│   ├── bulletins/
│   │   └── 2026-01-25.md
│   ├── css/
│   │   └── style.css
│   └── index.njk
├── admin/
│   └── config.yml (Decap CMS config)
├── netlify.toml
└── package.json
```

## Sample Bulletin Content (for testing)

Use the January 25, 2026 bulletin as the first test content:
- Sermon: "The Glue that Holds God's People Together" by Bobby Capps
- Scripture: Luke 6:27-36
- Theme: Love Your Enemies

## Deployment Notes

1. Set up Netlify site
2. Configure custom domain `bulletin.fpccorinth.org`
3. Enable Netlify Identity for CMS authentication
4. Add church staff as CMS users
5. SSL certificate (automatic with Netlify)

## Questions to Clarify with Church Staff
- Who needs CMS access?
- How far back should the archive go?
- Any existing branding guidelines beyond what's described?
- Should past bulletins be editable or locked after Sunday?
- Integration with main church website needed?

---

## Getting Started

1. Initialize the project with your preferred static site generator
2. Set up basic layout with mobile-first CSS
3. Create the bulletin template with all sections
4. Configure Decap CMS with the content fields
5. Add the first bulletin (Jan 25, 2026) as test content
6. Deploy to Netlify and test on mobile devices
7. Iterate on design based on readability testing

Focus on simplicity and readability first. This is meant to be glanced at during a worship service—it should be calm, clear, and distraction-free.
