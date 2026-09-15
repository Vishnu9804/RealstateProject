import { apiClient } from "./client";
import type { PropertyContentFields } from "./propertyApi";
import type { BuilderProjectRecord } from "./types";

/** How many projects every screen asks the list endpoint for. One shared
 *  number on purpose: the Builder Projects page and the match dialogs then
 *  request the SAME URL, so they share one copy in the browser's HTTP cache
 *  (a bodyless 304 when nothing changed) instead of each keeping its own. */
export const BUILDER_PROJECT_LIST_LIMIT = 500;

/** Mirrors Backend/Controller/BuilderProjectController/builder_project_controller.py
 *  — the Builder Projects page. The request bodies are exactly a property's
 *  (PropertyContentFields), because it is the same Add/Edit dialog. */
export const builderProjectApi = {
  /** The whole list, photo-less (image_urls is always [], image_count carries
   *  the real number). Answered with a bodyless 304 whenever the browser
   *  already holds the current list — the backend serves it from memory, so
   *  asking again costs neither side a database query. */
  getBuilderProjects: (limit = BUILDER_PROJECT_LIST_LIMIT): Promise<BuilderProjectRecord[]> =>
    apiClient.get(`/builder-projects?limit=${limit}`),
  /** One project, photo-less, from the backend's memory — for the match
   *  dialogs' read-only view of an assigned or visited builder project that
   *  isn't in the loaded list. 404 once the project has been deleted. */
  getBuilderProject: (recordId: string): Promise<BuilderProjectRecord> =>
    apiClient.get(`/builder-projects/${encodeURIComponent(recordId)}`),
  /** One project's photos — the only call that moves its image data, made
   *  only when someone presses Show photos. HTTP-cached per project until
   *  that project is edited. */
  getBuilderProjectImages: (recordId: string): Promise<{ image_urls: string[] }> =>
    apiClient.get(`/builder-projects/${encodeURIComponent(recordId)}/images`),
  createBuilderProject: (body: PropertyContentFields): Promise<BuilderProjectRecord> =>
    apiClient.post(`/builder-projects`, body),
  /** Send only what should change — in particular leave `image_urls` out
   *  unless the photos were loaded, or an edit would wipe them (see
   *  PropertyFormDialog's own toPayload). */
  updateBuilderProject: (recordId: string, body: PropertyContentFields): Promise<BuilderProjectRecord> =>
    apiClient.patch(`/builder-projects/${encodeURIComponent(recordId)}`, body),
  deleteBuilderProject: (recordId: string): Promise<void> =>
    apiClient.delete(`/builder-projects/${encodeURIComponent(recordId)}`),
};
