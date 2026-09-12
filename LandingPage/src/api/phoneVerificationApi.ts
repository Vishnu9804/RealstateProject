import { apiClient } from "./client";
import type { InquiryFormPrefill, OtpRequestResponse, OtpVerifyResponse } from "./types";

/**
 * The three calls behind the 4-digit code dialog — see
 * Backend/Controller/WhatsAppInquiryHandlingController/
 * phone_verification_controller.py and inquiry_form_controller.py.
 *
 * `requestCode` returns the instant the code is stored server-side; the
 * WhatsApp message itself is dispatched on a background thread there. That
 * is deliberate — the dialog opens with no wait, and the message lands
 * while the visitor is still looking at the empty boxes.
 *
 * `verifiedPrefill` is the public counterpart to inquiryFormApi.getPrefill,
 * and the reason it takes a token rather than a phone number is the whole
 * point of this flow: looking someone up by a number typed into a public
 * form would hand their saved requirements to anyone who guessed it, so
 * the lookup is keyed on proof of ownership instead.
 */
export const phoneVerificationApi = {
  /**
   * `priorToken` is whatever verification this browser is already holding
   * (lib/verifiedPhone.ts), sent so the server can recognise a number this
   * browser has ALREADY proved and answer "verified" instead of sending a
   * second message. Omitted when there is nothing stored, ignored when it
   * is for a different number or has expired — it can only ever remove a
   * WhatsApp send, never authorise anything.
   */
  requestCode: (phone: string, priorToken?: string | null): Promise<OtpRequestResponse> =>
    apiClient.post("/whatsapp-inquiry/verify/request", {
      phone,
      verification_token: priorToken ?? null,
    }),

  confirmCode: (phone: string, code: string): Promise<OtpVerifyResponse> =>
    apiClient.post("/whatsapp-inquiry/verify/confirm", { phone, code }),

  verifiedPrefill: (verificationToken: string): Promise<InquiryFormPrefill> =>
    apiClient.post("/whatsapp-inquiry/form/public/prefill", { verification_token: verificationToken }),
};
