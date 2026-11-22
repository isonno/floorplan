# Floorplan

A dependency-free, local residential space planner. It models rooms as rounded
rectangles, not construction details.

This is a vibe-coded application; see [prompt.md](prompt.md) for the specification given to Copilot to create the app.

## Run

Requires Python 3.9 or later and a current desktop browser. No Node.js, packages,
or build step are needed. CSS and JavaScript are embedded in `floorplan.html`.

From this folder:

```sh
python3 floorplan.py
```

On Windows, use `py floorplan.py`. Your browser opens automatically. Keep the
terminal open; use **Quit** in the editor or press Ctrl+C there to stop the server. To open the browser yourself,
use `python3 floorplan.py --no-browser`. An available loopback port is chosen
automatically; `--port 8000` selects a specific port.

## Use

- **Load Background:** enter a local PNG, JPEG, GIF, WebP, or BMP pathname.
  Paths may contain spaces; enter them without enclosing quotes. Relative paths
  start in this application folder; `~` expands to the user's home directory.
  Images are rendered over white at 25% opacity. The limit is 50 MB.
- **Set Scale:** enter a known distance in feet, then drag that distance on the
  image. The measuring line locks to the dominant horizontal or vertical axis.
  The initial scale is 20 image pixels per foot. Recalibration changes the
  image-to-feet relationship, not existing room dimensions.
- **New Space:** enter a name, then drag opposite corners. Every edge snaps to
  half-foot intervals. The current square footage appears in the center while
  dragging. The workspace extends as rooms approach its bounds.
- **Delete Space:** removes the selected space (the one showing drag handles).
  It is disabled when no space is selected. The total area updates immediately.
- Drag inside a space to move it. Selecting a space reveals eight handles.
  Side midpoint handles change its area freely; corner handles keep its area
  within +/- 2 sqft of its value at the start of each drag and keep the opposite
  corner fixed.
- Corner resizing chooses the half-foot width/height pair nearest the cursor
  within that area range. Snapping can still cause jumps between valid proportions.
- Related names share a color family: `MasterBR.bath` and `MasterBR.closet`
  use a darker version of `MasterBR`'s color, even if created before the parent.
- **Grid** toggles the one-foot grid. Above 150% zoom, dotted half-foot lines
  also appear. **Background** toggles the loaded image. Zoom affects every layer.
  Use the workspace scroll bars or a trackpad to pan. Escape cancels drawing or
  restores a room to its state before the current drag.
- **Total square feet**, below Zoom, sums the areas of all spaces and updates
  as they change. Overlapping spaces each contribute their full area.
- **Save Project / Load Project:** enter a local JSON pathname. Saving confirms
  before replacing a file and writes atomically. The containing folder must
  already exist. Rooms, colors, background pathname, scale, zoom, layer visibility,
  and scroll position are saved. Unsaved changes trigger a warning on closing.
- **Quit:** the button at the bottom of the control panel stops the Python server.
  If there are unsaved changes, choose **Save**, **Don't Save**, or **Cancel**.
  Canceling a save or a save failure leaves the application running.
  Browsers may prevent a page from closing its own tab; in that case, the tab
  displays "Application stopped" and can be closed manually.

Projects reference their background file; they do not embed it. Keep that image
available when reopening a project. Saved background paths are absolute; when
moving projects between computers, update `backgroundPath` in the JSON to the new
path, or a relative path beside the project. Missing or invalid backgrounds produce
an explicit error and leave the currently open plan unchanged.

The white control panel fits the Zoom control with approximately twice its left
margin beyond its right edge, and stays fixed when the window is resized.
All additional width goes to the drawing. Its controls scroll vertically if needed,
while Quit stays at the bottom. The drawing starts empty; the illustration is a layout guide, not
preloaded project content.

## Files and local access

`floorplan.py` serves only the editor and explicitly loaded images. It binds to
`127.0.0.1`, validates the request host and session token, and does not enable CORS.
Path dialogs intentionally allow access to files your user account can read or
write. Do not expose this development server to the network. JSON projects are
validated (at most 2 MB and 2,000 rooms); space coordinates and extents are limited
to a 10,000-foot-square workspace.

Run the server/persistence tests with:

```sh
python3 -m unittest -v test_floorplan.py
```
