import { EditorSelection } from "@codemirror/state";
import { EditorView, ViewPlugin } from "@codemirror/view";

const tapDuration = 250;
const tapMovement = 8;
const repeatInterval = 400;
const repeatDistance = 24;

function distance(touch, point) {
  return Math.hypot(touch.clientX - point.x, touch.clientY - point.y);
}

export const preciseTouchSelection = ViewPlugin.fromClass(class {
  constructor(view) {
    this.view = view;
    this.gesture = null;
    this.previousTap = null;
    this.timer = null;
  }

  cancel() {
    this.gesture = null;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }

  canPlaceCursor() {
    const { view } = this;
    return !view.state.readOnly && view.state.facet(EditorView.editable)
      && !view.compositionStarted && !view.composing
      && view.state.selection.ranges.length === 1 && view.state.selection.main.empty;
  }

  start(event) {
    this.cancel();
    if (event.touches.length !== 1 || !this.canPlaceCursor()
      || !this.view.contentDOM.contains(event.target)) return;
    const touch = event.touches[0];
    // Resolve before focus/keyboard changes can move the editor's viewport.
    const position = this.view.posAtCoords({ x: touch.clientX, y: touch.clientY });
    if (position === null) return;
    const before = this.view.lineWrapping ? this.view.coordsAtPos(position, -1) : null;
    this.gesture = {
      id: touch.identifier, x: touch.clientX, y: touch.clientY,
      time: event.timeStamp, doc: this.view.state.doc, position,
      initialCursor: this.view.state.selection.main,
      assoc: before ? (touch.clientY <= before.bottom ? -1 : 1) : 0,
      repeated: this.previousTap !== null
        && event.timeStamp - this.previousTap.time < repeatInterval
        && distance(touch, this.previousTap) < repeatDistance,
    };
  }

  move(event) {
    const touch = event.touches[0];
    if (this.gesture && (event.touches.length !== 1
      || touch.identifier !== this.gesture.id || distance(touch, this.gesture) > tapMovement)) {
      this.cancel();
    }
  }

  end(event) {
    const tap = this.gesture;
    this.gesture = null;
    const touch = event.changedTouches[0];
    if (!tap || event.touches.length || !touch || touch.identifier !== tap.id
      || event.timeStamp - tap.time > tapDuration || distance(touch, tap) > tapMovement) return;
    this.previousTap = { x: tap.x, y: tap.y, time: event.timeStamp };
    if (tap.repeated || !this.canPlaceCursor()) return;
    const endCursor = this.view.state.selection.main;
    const nativeCursor = !endCursor.eq(tap.initialCursor, true) ? endCursor : null;

    // Keep the caret placed under the finger. Coordinate lookup is only a fallback
    // when native selection never moved, not a second placement after release.
    this.timer = setTimeout(() => {
      this.timer = null;
      const { view } = this;
      if (!view.hasFocus || view.state.doc !== tap.doc || !this.canPlaceCursor()) return;
      if (!nativeCursor && !view.state.selection.main.eq(tap.initialCursor, true)) return;
      const cursor = nativeCursor || EditorSelection.cursor(tap.position, tap.assoc);
      if (!view.state.selection.main.eq(cursor, true)) {
        view.dispatch({ selection: EditorSelection.create([cursor]), userEvent: "select.pointer" });
      }
    }, 50);
  }

  update(update) {
    if (update.docChanged) this.cancel();
  }

  destroy() {
    this.cancel();
  }
}, {
  eventObservers: {
    touchstart(event) { this.start(event); },
    touchmove(event) { this.move(event); },
    touchend(event) { this.end(event); },
    touchcancel() { this.cancel(); },
    scroll() { this.gesture = null; },
    blur() { this.cancel(); },
    keydown() { this.cancel(); },
    beforeinput() { this.cancel(); },
    compositionstart() { this.cancel(); },
  },
});
