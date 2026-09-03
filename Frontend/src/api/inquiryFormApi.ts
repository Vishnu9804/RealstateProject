import { apiClient } from "./client";
import type { InquiryFormPrefill, InquiryFormSubmission } from "./types";

/**
 * The public, token-authenticated registration/update form — see
 * Backend/Controller/WhatsAppInquiryHandlingController/inquiry_form_controller.py.
 * No auth beyond the token itself: it's what the WhatsApp welcome/update
 * message links to, so it must work for a client who has never opened this
 * app before and never will again.
 */
export const inquiryFormApi = {
  getPrefill: (token: string): Promise<InquiryFormPrefill> =>
    apiClient.get(`/whatsapp-inquiry/form/${encodeURIComponent(token)}`),

  submit: (token: string, submission: InquiryFormSubmission): Promise<{ status: string }> =>
    apiClient.post(`/whatsapp-inquiry/form/${encodeURIComponent(token)}`, submission),
};
