import { useNavigate } from "react-router-dom";
import { scrollToSection } from "../hooks/useScroll";
import { site } from "../lib/siteConfig";
import { SECTIONS } from "./SiteHeader";
import { IconChat } from "./Icons";

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
          <button type="button" onClick={() => goToSection("contact")}>
            Enquire
          </button>
        </nav>

        <span>
          © {new Date().getFullYear()} {site.brand} {site.brandAccent} · {site.city}
        </span>
      </div>

      {/* Always reachable, because telling us what you're after is the one
          thing this whole site is for — and someone who becomes ready to do
          that three sections down shouldn't have to hunt for the way to.
          It used to open a wa.me link built from a placeholder number; it
          now goes to the actual form, and says something different from the
          hero's button on purpose (two identical labels on one screen read
          as one control duplicated by mistake). */}
      <button type="button" className="cta-fab" onClick={() => goToSection("contact")}>
        <IconChat size={18} />
        <span>Tell us about your requirements</span>
      </button>
    </footer>
  );
}
