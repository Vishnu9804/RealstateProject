/**
 * The world the interface floats in.
 *
 * This exists so depth has something to be depth *against*. Panels with
 * shadows over a flat colour still read as flat; the same panels over a
 * receding grid and slow drifting light read as objects held in front of a
 * space. Purely decorative — fixed, inert and
 * `aria-hidden`, so nothing here reaches the accessibility tree or
 * intercepts a click.
 */
export default function Scene() {
  return (
    <div className="scene" aria-hidden="true">
      <div className="scene__base" />
      <div className="scene__aurora">
        <span className="scene__blob scene__blob--1" />
        <span className="scene__blob scene__blob--2" />
        <span className="scene__blob scene__blob--3" />
      </div>
      <div className="scene__ceiling" />
      <div className="scene__floor" />
      <div className="scene__noise" />
    </div>
  );
}
