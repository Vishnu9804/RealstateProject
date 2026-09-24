import { useEffect } from "react";
import { freezeScene } from "../lib/sceneFreeze";

/** Holds the background scene still for as long as the calling component is
 *  mounted — see lib/sceneFreeze.ts for what that buys and why it is
 *  ref-counted. Used by the two full-screen matches dialogs. */
export function useSceneFreeze(): void {
  useEffect(() => freezeScene(), []);
}
