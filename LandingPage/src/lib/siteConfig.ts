/**
 * Everything about the site that is the CLIENT'S, not the code's: the name
 * on the door, the phone number, the sales copy.
 *
 * Collected in one file on purpose. Every one of these is a thing the
 * client will want changed at some point, and none of them is a thing that
 * should require reading a component to change. Edit here and it updates
 * everywhere it appears.
 */

export const site = {
  brand: "Aurum",
  brandAccent: "Estates",
  city: "Surat",

  /**
   * PLACEHOLDER — replace with the client's real WhatsApp number in
   * international format, digits only (country code, no "+", no spaces).
   * It is used to build wa.me links, which is what every "Chat on WhatsApp"
   * button on the site opens.
   */
  whatsappNumber: "919999999999",

  /** Prefilled first message on those wa.me links. */
  whatsappGreeting: "Hi! I found your website and I'd like to know more about a property.",

  hero: {
    eyebrow: "Curated residences",
    titleLead: "Homes worth",
    titleAccent: "coming home to",
    lede: "A small, hand-checked collection of apartments, plots and rentals — every listing visited, photographed and priced honestly. No walls of dead listings, no chasing. Tell us what you want, and we bring you the three that fit.",
  },

  stats: [
    { value: "10+", label: "Years in the market" },
    { value: "1,200+", label: "Families settled" },
    { value: "48 hrs", label: "Typical first viewing" },
  ],

  about: {
    paragraphs: [
      "We are a small local team, and we like it that way. Every property on this page has been walked through by one of us before it ever reaches your screen — the photographs are ours, the measurements are checked, and the price is the one the owner will actually take.",
      "What that means for you is simple: fewer listings, none of them wasted. You tell us the shape of the life you're trying to build, and we do the filtering long before you're asked to give up a Sunday.",
    ],
    points: [
      "Every listing personally verified before it is published",
      "Real photographs and walkthrough reels — never stock imagery",
      "Straight answers on price, paperwork and possession",
      "One point of contact from first message to handover",
    ],
  },

  process: [
    {
      icon: "chat" as const,
      title: "Tell us what you need",
      body: "Send your name and number. One message with the shape of what you're looking for is enough to start.",
    },
    {
      icon: "search" as const,
      title: "We shortlist for you",
      body: "We match your budget and locality against what we've actually seen, and come back with a handful worth your time.",
    },
    {
      icon: "shield" as const,
      title: "Visit with clarity",
      body: "You see the place, the paperwork and the real numbers together. No pressure, no surprises later.",
    },
    {
      icon: "key" as const,
      title: "Close and move in",
      body: "We stay with the deal through documentation, registration and handover — the same person, start to finish.",
    },
  ],

  contact: {
    title: "Let's find yours",
    lede: "Leave your name and WhatsApp number. We reply personally, usually within a few hours, and never pass your number on to anyone.",
  },
} as const;

/** The wa.me link every "Chat on WhatsApp" control on the site opens. */
export function whatsappLink(message: string = site.whatsappGreeting): string {
  return `https://wa.me/${site.whatsappNumber}?text=${encodeURIComponent(message)}`;
}
