import { useNavigate } from "react-router-dom";
import { scrollToSection } from "../hooks/useScroll";
import { site, whatsappLink } from "../lib/siteConfig";
import { SECTIONS } from "./SiteHeader";
import { IconWhatsApp } from "./Icons";

export default function SiteFooter({ onHome }: { onHome: boolean }) {
  const navigate = useNavigate();

  function goToSection(id: string) {
    if (onHome) scrollToSection(id);
    else navigate("/", { state: { scrollTo: id } });
  }

  return (
    <footer className="site-footer">
      <div className="shell site-footer__inner">
        <div className="brand">
          <span className="brand__mark">{site.brand.charAt(0)}</span>
          <span className="brand__text">
            {site.brand}
            <em>{site.brandAccent}</em>
          </span>
        </div>

        <nav className="site-footer__links" aria-label="Footer">
          {SECTIONS.map((section) => (
            <button key={section.id} type="button" onClick={() => goToSection(section.id)}>
              {section.label}
            </button>
          ))}
        </nav>

        <span>
          © {new Date().getFullYear()} {site.brand} {site.brandAccent} · {site.city}
        </span>
      </div>

      {/* Always reachable, because starting a WhatsApp conversation is the
          single thing this whole site is for. */}
      <a className="wa-fab" href={whatsappLink()} target="_blank" rel="noreferrer noopener">
        <IconWhatsApp />
        <span>Chat on WhatsApp</span>
      </a>
    </footer>
  );
}
