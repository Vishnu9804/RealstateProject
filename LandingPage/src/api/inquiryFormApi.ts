import { apiClient } from "./client";
import type { InquiryFormPrefill, InquiryFormResult, InquiryFormSubmission } from "./types";

/**
 * The requirements form's three calls — see
 * Backend/Controller/WhatsAppInquiryHandlingController/inquiry_form_controller.py.
 *
 * Two doors into the same form, and the difference matters:
 *
 *  - `getPrefill`/`submit` take a token. The token was minted for one
 *    person (the WhatsApp number we messaged, or the Instagram account we
 *    DMed) and IS their identity — nothing typed into the page can change
 *    who the submission is attributed to.
 *  - `submitPublic` has no token, because a stranger who found this site on
 *    their own has no link of ours. Their identity is the WhatsApp number
 *    they give — and, since it has to come from the browser, it is one they
 *    have proven they own by typing back a 4-digit code we sent to it
 *    (api/phoneVerificationApi.ts). That `verification_token` outranks the
 *    typed number server-side.
 *
 * There is still deliberately no public prefill *by phone number*: looking
 * someone up by a number typed into a public form would hand their saved
 * requirements to anyone who guessed it. The verified equivalent lives in
 * phoneVerificationApi.verifiedPrefill, keyed on proof of ownership rather
 * than on the number itself — which is the entire distinction.
 */
export const inquiryFormApi = {
  getPrefill: (token: string): Promise<InquiryFormPrefill> =>
    apiClient.get(`/whatsapp-inquiry/form/${encodeURIComponent(token)}`),

  submit: (token: string, submission: InquiryFormSubmission): Promise<InquiryFormResult> =>
    apiClient.post(`/whatsapp-inquiry/form/${encodeURIComponent(token)}`, submission),

  submitPublic: (submission: InquiryFormSubmission): Promise<InquiryFormResult> =>
    apiClient.post("/whatsapp-inquiry/form/public", submission),
};
