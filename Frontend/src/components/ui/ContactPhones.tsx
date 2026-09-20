import { Copyable } from "./Primitives";
import { formatPhone, phoneList, type HasContactPhones } from "../../lib/phone";

/**
 * The two ways a listing's contact numbers are ever shown, in one place so
 * every table, card and dialog in the application shows them identically.
 *
 * A listing now carries a LIST of numbers (see lib/phone.ts), and the two
 * places it appears want opposite things from that list:
 *
 * - A ROW IN A LIST has room for one line and is read by the dozen while
 *   scanning. It shows the primary number and a "+2" to say there are more,
 *   which keeps every row the same height — the thing that made the list
 *   scannable in the first place — while never pretending the others do not
 *   exist. <ContactPhoneSummary/>.
 * - THE DETAIL DIALOG the row opens has room for all of them and is where
 *   somebody goes to actually call. It shows every number, each separately
 *   copyable. <ContactPhoneDetails/>.
 *
 * Both copy the number as STORED ("+919824750171") while displaying the
 * spaced form, because what lands in the clipboard is pasted into WhatsApp
 * or a dialler, where the space is at best noise.
 */

/** The primary number plus a "+N" when there are others — one line, fixed
 *  height, for a table cell or a card. Renders nothing at all when there is
 *  no number, so a caller can drop it straight into its layout. */
export function ContactPhoneSummary({
  record,
  children,
}: {
  record: HasContactPhones | null | undefined;
  /** What to show INSTEAD of the number on the copy button — used where the
   *  line already reads "Name · number". The "+N" still follows it. */
  children?: (primary: string) => React.ReactNode;
}) {
  const numbers = phoneList(record);
  if (numbers.length === 0) return null;
  const [primary, ...rest] = numbers;
  return (
    <>
      <Copyable text={primary}>{children ? children(formatPhone(primary)) : formatPhone(primary)}</Copyable>
      {rest.length > 0 && (
        <span
          className="faint small"
          style={{ marginLeft: 6, fontWeight: 640 }}
          // The other numbers are one hover away even here, so the
          // summary never hides information that only the dialog holds.
          title={`${rest.length} more number${rest.length > 1 ? "s" : ""}: ${rest.map(formatPhone).join(", ")}`}
        >
          +{rest.length}
        </span>
      )}
    </>
  );
}

/** Every number, stacked, each one copyable — for a detail dialog. */
export function ContactPhoneDetails({ record }: { record: HasContactPhones | null | undefined }) {
  const numbers = phoneList(record);
  if (numbers.length === 0) return null;
  return (
    <>
      {numbers.map((value) => (
        <div key={value} className="detail__v" style={{ marginTop: 4 }}>
          <Copyable text={value}>{formatPhone(value)}</Copyable>
        </div>
      ))}
    </>
  );
}
