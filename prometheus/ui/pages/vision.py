"""Vision page: a 3D (or top-down) view of what the agent is working with.

Everything drawn comes from the bridge: the terrain is the blocks around the bot, the route
and the examined positions are the pathfinder's own A* output, and the markers are the
positions returned to Claude by its observation tools.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from ...agent.runner import AgentRunner
from ...context import AppContext
from .. import theme
from ..widgets import Card, label

RADIUS = 28
RADIUS_3D = 16
PATH = "#ff4fd8"
EXPLORED = "#35d0e0"
SEEN = "#ffb020"
TARGET = "#ffffff"

_COLOURS = (
    (("water", "bubble_column", "kelp", "seagrass"), "#2f6fd6"), (("lava", "magma"), "#e2581d"),
    (("_ore",), "#c9b458"), (("leaves", "vine", "moss", "bamboo"), "#2f6b2a"),
    (("grass_block",), "#4f8f3a"), (("grass", "fern", "flower", "tulip", "dandelion", "poppy", "bush"), "#5fa044"),
    (("log", "planks", "wood", "stem", "crafting_table", "chest", "fence", "door"), "#8a6a3c"),
    (("sand", "sandstone"), "#d8cc8c"), (("snow", "ice"), "#dfe9f2"), (("gravel",), "#8a8580"),
    (("dirt", "podzol", "mud", "farmland", "path", "clay"), "#6e4f33"),
    (("furnace", "torch", "lantern", "glowstone"), "#e08a3a"),
    (("stone", "cobble", "deepslate", "andesite", "granite", "diorite", "tuff", "basalt", "brick", "bedrock"), "#7d7f85"),
)


def block_colour(name: str) -> str:
    for keys, colour in _COLOURS:
        if any(k in name for k in keys):
            return colour
    return "#6b7280"


class MapView(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(420, 420)
        self.map: dict | None = None
        self.path: list[list[float]] = []
        self.explored: list[list[float]] = []
        self.seen: list[tuple[float, float, float]] = []       # positions returned to Claude by observation tools
        self.target: tuple[float, float, float] | None = None  # where the current action is aimed
        self.show_path = self.show_search = self.show_seen = self.show_entities = True
        self.setAccessibleName("Top-down map of the area around the agent")

    def clear(self) -> None:
        self.map, self.path, self.explored, self.seen, self.target = None, [], [], [], None
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.BG))
        m = self.map
        if not m:
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Connect to a server to see the area around the agent.")
            return p.end()
        r, side = m["radius"], 2 * m["radius"] + 1
        cs = min(self.width(), self.height()) / side
        ox, oz = (self.width() - cs * side) / 2, (self.height() - cs * side) / 2
        x0, z0 = m["center"]["x"] - r, m["center"]["z"] - r

        def at(x: float, z: float) -> QPointF:      # world column -> centre of its cell
            return QPointF(ox + (x - x0 + 0.5) * cs, oz + (z - z0 + 0.5) * cs)

        colours = [QColor(block_colour(n)) for n in m["palette"]]
        p.setPen(Qt.PenStyle.NoPen)
        for i, cell in enumerate(m["cells"]):
            if cell < 0:
                continue
            shade = max(60, min(150, 100 + m["heights"][i] * 4))      # higher ground is lighter
            p.setBrush(colours[cell].lighter(shade) if shade >= 100 else colours[cell].darker(200 - shade))
            p.drawRect(QRectF(ox + (i % side) * cs, oz + (i // side) * cs, cs + 0.6, cs + 0.6))

        if self.show_search and self.explored:
            c = QColor(EXPLORED)
            c.setAlpha(120)
            p.setBrush(c)
            d = max(2.0, cs * 0.45)
            for x, _y, z in self.explored:
                pt = at(x, z)
                p.drawRect(QRectF(pt.x() - d / 2, pt.y() - d / 2, d, d))
        if self.show_seen:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(SEEN), 1.6))
            for x, _y, z in self.seen:
                pt = at(x, z)
                p.drawRect(QRectF(pt.x() - cs * 0.6, pt.y() - cs * 0.6, cs * 1.2, cs * 1.2))
        if self.show_path and len(self.path) > 1:
            p.setPen(QPen(QColor(PATH), max(2.0, cs * 0.35), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolyline(QPolygonF([at(x, z) for x, _y, z in self.path]))
            end = at(self.path[-1][0], self.path[-1][2])
            p.setBrush(QColor(PATH))
            p.drawEllipse(end, cs * 0.6, cs * 0.6)
        if self.target is not None:
            pt = at(self.target[0], self.target[2])
            p.setPen(QPen(QColor(TARGET), 1.8))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(pt, cs * 1.1, cs * 1.1)
            p.drawLine(QPointF(pt.x() - cs * 1.7, pt.y()), QPointF(pt.x() + cs * 1.7, pt.y()))
            p.drawLine(QPointF(pt.x(), pt.y() - cs * 1.7), QPointF(pt.x(), pt.y() + cs * 1.7))
        if self.show_entities:
            p.setPen(QPen(QColor("#101010"), 1))
            for e in m.get("entities", []):
                kind = str(e.get("kind", "")).lower()
                p.setBrush(QColor("#ffffff" if kind == "player" else theme.BAD if "hostile" in kind else "#ffe066"))
                p.drawEllipse(at(e["x"] - 0.5, e["z"] - 0.5), max(2.5, cs * 0.4), max(2.5, cs * 0.4))
        # the bot: an arrow pointing where it faces (yaw 0 is north, counter-clockwise positive)
        centre = at(m["center"]["x"], m["center"]["z"])
        yaw = m.get("yaw", 0.0)
        fx, fz = -math.sin(yaw), -math.cos(yaw)
        size = max(7.0, cs * 1.1)
        tip = QPointF(centre.x() + fx * size, centre.y() + fz * size)
        left = QPointF(centre.x() - fx * size * 0.6 - fz * size * 0.6, centre.y() - fz * size * 0.6 + fx * size * 0.6)
        right = QPointF(centre.x() - fx * size * 0.6 + fz * size * 0.6, centre.y() - fz * size * 0.6 - fx * size * 0.6)
        p.setPen(QPen(QColor("#101010"), 1.2))
        p.setBrush(QColor(theme.ACCENT))
        p.drawPolygon(QPolygonF([tip, left, right]))
        p.end()


# face bit -> (normal, the four corners of that face of a unit cube, brightness)
_FACES = (
    (1, (1, 0, 0), ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1)), 92),
    (2, (-1, 0, 0), ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)), 92),
    (4, (0, 1, 0), ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)), 118),
    (8, (0, -1, 0), ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)), 55),
    (16, (0, 0, 1), ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)), 76),
    (32, (0, 0, -1), ((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0)), 76),
)
_CUBE_EDGES = ((0, 1), (1, 3), (3, 2), (2, 0), (4, 5), (5, 7), (7, 6), (6, 4), (0, 4), (1, 5), (2, 6), (3, 7))


class View3D(QWidget):
    """Software-rendered 3D view: perspective projection with depth-sorted block faces.

    Drag to orbit, scroll to zoom. Coordinates are Minecraft's (x east, y up, z south),
    relative to the block the bot stands in.
    """

    def __init__(self):
        super().__init__()
        self.setMinimumSize(420, 420)
        self.data: dict | None = None
        self.path: list[list[float]] = []
        self.explored: list[list[float]] = []
        self.seen: list[tuple[float, float, float]] = []
        self.target: tuple[float, float, float] | None = None
        self.show_path = self.show_search = self.show_seen = self.show_entities = True
        self.yaw, self.pitch, self.distance = math.radians(215), math.radians(38), 30.0
        self._drag: QPointF | None = None
        self.faces_drawn = 0
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setAccessibleName("3D view of the area around the agent. Drag to rotate, scroll to zoom.")

    def clear(self) -> None:
        self.data, self.path, self.explored, self.seen, self.target = None, [], [], [], None
        self.update()

    # ---- camera ----
    def mousePressEvent(self, event) -> None:
        self._drag = event.position()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, _event) -> None:
        self._drag = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseMoveEvent(self, event) -> None:
        if self._drag is None:
            return
        delta = event.position() - self._drag
        self._drag = event.position()
        self.orbit(-delta.x() * 0.008, delta.y() * 0.006)

    def wheelEvent(self, event) -> None:
        self.distance = max(6.0, min(70.0, self.distance * (0.88 if event.angleDelta().y() > 0 else 1.14)))
        self.update()

    def orbit(self, d_yaw: float, d_pitch: float) -> None:
        self.yaw = (self.yaw + d_yaw) % (2 * math.pi)
        self.pitch = max(math.radians(4), min(math.radians(88), self.pitch + d_pitch))
        self.update()

    def _camera(self):
        """Returns (camera position, right, up, forward) for the current orbit."""
        d = self.data
        tx = d["position"]["x"] - d["center"]["x"]
        ty = d["position"]["y"] - d["center"]["y"] + 0.9
        tz = d["position"]["z"] - d["center"]["z"]
        cp, sp, cy, sy = math.cos(self.pitch), math.sin(self.pitch), math.cos(self.yaw), math.sin(self.yaw)
        cam = (tx + self.distance * cp * sy, ty + self.distance * sp, tz + self.distance * cp * cy)
        f = (-cp * sy, -sp, -cp * cy)                     # unit vector towards the target
        rl = math.hypot(f[2], f[0]) or 1.0
        r = (-f[2] / rl, 0.0, f[0] / rl)                  # forward x up
        u = (r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0])
        return cam, r, u, f

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0d1117"))
        d = self.data
        if not d:
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Connect to a server to see the area around the agent.")
            self.faces_drawn = 0
            return p.end()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cam, r, u, f = self._camera()
        cx, cy, focal = self.width() / 2, self.height() / 2, min(self.width(), self.height()) * 0.95
        camx, camy, camz = cam
        rx, ry, rz = r
        ux, uy, uz = u
        fx, fy, fz = f

        def project(x: float, y: float, z: float):
            vx, vy, vz = x - camx, y - camy, z - camz
            depth = vx * fx + vy * fy + vz * fz
            if depth < 0.3:
                return None
            k = focal / depth
            return cx + (vx * rx + vy * ry + vz * rz) * k, cy - (vx * ux + vy * uy + vz * uz) * k, depth

        corner: dict[tuple[int, int, int], tuple | None] = {}

        def grid(x: int, y: int, z: int):
            key = (x, y, z)
            if key not in corner:
                corner[key] = project(x, y, z)
            return corner[key]

        faces = []
        for bx, by, bz, pi, mask in d["blocks"]:
            for bit, (nx, ny, nz), corners, light in _FACES:
                if not mask & bit:
                    continue
                # back-face cull: skip faces pointing away from the camera
                if (camx - bx - 0.5 - nx * 0.5) * nx + (camy - by - 0.5 - ny * 0.5) * ny + (camz - bz - 0.5 - nz * 0.5) * nz <= 0:
                    continue
                pts = [grid(bx + ox, by + oy, bz + oz) for ox, oy, oz in corners]
                if None in pts:
                    continue
                faces.append((pts[0][2] + pts[2][2], pts, pi, light))
        faces.sort(key=lambda item: -item[0])              # painter's algorithm: far to near
        self.faces_drawn = len(faces)
        base = [QColor(block_colour(n)) for n in d["palette"]]
        shades: dict[tuple[int, int], QColor] = {}
        liquid = {i for i, n in enumerate(d["palette"]) if n in ("water", "lava")}
        p.setPen(QPen(QColor(0, 0, 0, 40), 0.6))
        for _depth, pts, pi, light in faces:
            colour = shades.get((pi, light))
            if colour is None:
                colour = base[pi].lighter(light) if light >= 100 else base[pi].darker(int(10000 / light))
                if pi in liquid:
                    colour.setAlpha(170)
                shades[(pi, light)] = colour
            p.setBrush(colour)
            p.drawPolygon(QPolygonF([QPointF(x, y) for x, y, _ in pts]))

        ox, oy, oz = d["center"]["x"], d["center"]["y"], d["center"]["z"]

        def world(x: float, y: float, z: float):
            return project(x - ox, y - oy, z - oz)

        def box(x: float, y: float, z: float, w: float, h: float, colour: str, width: float = 1.6) -> None:
            pts = [world(x + dx * w, y + dy * h, z + dz * w) for dx in (0, 1) for dy in (0, 1) for dz in (0, 1)]
            if None in pts:
                return
            p.setPen(QPen(QColor(colour), width))
            for a, b in _CUBE_EDGES:
                p.drawLine(QPointF(pts[a][0], pts[a][1]), QPointF(pts[b][0], pts[b][1]))

        # Overlays are drawn on top of the terrain, so a route stays visible behind a hill.
        if self.show_search and self.explored:
            colour = QColor(EXPLORED)
            colour.setAlpha(150)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(colour)
            for x, y, z in self.explored:
                pt = world(x + 0.5, y + 0.15, z + 0.5)
                if pt:
                    size = max(1.5, focal / pt[2] * 0.22)
                    p.drawRect(QRectF(pt[0] - size / 2, pt[1] - size / 2, size, size))
        if self.show_seen:
            for x, y, z in self.seen:
                box(x, y, z, 1, 1, SEEN)
        if self.show_path and len(self.path) > 1:
            pts = [world(x + 0.5, y + 0.2, z + 0.5) for x, y, z in self.path]
            line = [QPointF(pt[0], pt[1]) for pt in pts if pt]
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(PATH), 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawPolyline(QPolygonF(line))
            if pts[-1]:
                x, y, z = self.path[-1]
                box(x, y, z, 1, 2, PATH, 2.2)             # the goal, as a player-sized box
        if self.target is not None:
            x, y, z = self.target
            foot, top = world(x + 0.5, y, z + 0.5), world(x + 0.5, y + 7, z + 0.5)
            if foot and top:                              # a beam above the current action's target
                p.setPen(QPen(QColor(TARGET), 2.4))
                p.drawLine(QPointF(foot[0], foot[1]), QPointF(top[0], top[1]))
            box(x, y, z, 1, 1, TARGET, 2.0)
        if self.show_entities:
            for e in d.get("entities", []):
                kind = str(e.get("kind", "")).lower()
                colour = "#ffffff" if kind == "player" else theme.BAD if "hostile" in kind else "#ffe066"
                box(e["x"] - 0.3, e["y"], e["z"] - 0.3, 0.6, max(0.4, float(e.get("height", 1))), colour, 1.4)
        # the agent itself, with a line showing where it faces (yaw 0 is north, counter-clockwise positive)
        pos = d["position"]
        box(pos["x"] - 0.3, pos["y"], pos["z"] - 0.3, 0.6, 1.8, theme.ACCENT, 2.4)
        yaw = d.get("yaw", 0.0)
        eye = world(pos["x"], pos["y"] + 1.6, pos["z"])
        ahead = world(pos["x"] - math.sin(yaw) * 1.6, pos["y"] + 1.6, pos["z"] - math.cos(yaw) * 1.6)
        if eye and ahead:
            p.setPen(QPen(QColor(theme.ACCENT), 2.4))
            p.drawLine(QPointF(eye[0], eye[1]), QPointF(ahead[0], ahead[1]))
        p.end()


class VisionPage(QWidget):
    def __init__(self, ctx: AppContext, runner: AgentRunner):
        super().__init__()
        self.ctx, self.runner = ctx, runner
        self._spawned = False
        self._busy = False
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        root.addWidget(label("Vision", "PageTitle"))
        row = QHBoxLayout()
        row.setSpacing(14)
        card = Card("Around the agent")
        head = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItem("3D view", "3d")
        self.mode.addItem("Top-down map", "map")
        self.mode.setAccessibleName("View mode")
        self.hint = label("", "Muted")
        head.addWidget(self.mode)
        head.addWidget(self.hint, 1)
        card.body.addLayout(head)
        self.view3d = View3D()
        self.view = MapView()
        self.stack = QStackedWidget()
        self.stack.addWidget(self.view3d)
        self.stack.addWidget(self.view)
        card.body.addWidget(self.stack, 1)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        row.addWidget(card, 3)

        side = QVBoxLayout()
        layers = Card("Layers")
        self.toggles = {}
        for key, text, colour in (("show_path", "Route the pathfinder chose", PATH),
                                  ("show_search", "Positions its search examined", EXPLORED),
                                  ("show_seen", "Blocks reported to Claude", SEEN),
                                  ("show_entities", "Players and mobs", "#ffe066")):
            box = QCheckBox(text)
            box.setChecked(True)
            box.setStyleSheet(f"QCheckBox {{ color: {colour}; }}")
            box.toggled.connect(lambda on, k=key: self._toggle(k, on))
            layers.body.addWidget(box)
            self.toggles[key] = box
        side.addWidget(layers)
        nav = Card("Pathfinding")
        self.path_info = label("No route has been planned yet.", wrap=True, selectable=True)
        nav.body.addWidget(self.path_info)
        side.addWidget(nav)
        seen = Card("Claude's latest observation")
        self.seen_info = label("Nothing yet. Markers appear when the agent scans, searches for blocks or picks a "
                               "target.", "Muted", wrap=True, selectable=True)
        seen.body.addWidget(self.seen_info)
        side.addWidget(seen)
        self.legend = label("", "Muted", wrap=True)
        side.addWidget(self.legend)
        side.addStretch(1)
        holder = QWidget()
        holder.setLayout(side)
        holder.setMaximumWidth(330)
        row.addWidget(holder, 1)
        root.addLayout(row, 1)

        self.timer = QTimer(self)
        self.timer.setInterval(1200)
        self.timer.timeout.connect(self.refresh)
        ctx.bridge.notification.connect(self._on_notification)
        ctx.bridge.runningChanged.connect(lambda running: None if running else self._gone())
        runner.event.connect(self._on_event)
        runner.observed.connect(self._on_observed)
        self._mode_changed()

    @property
    def views(self):
        return (self.view3d, self.view)

    def _toggle(self, key: str, on: bool) -> None:
        for view in self.views:
            setattr(view, key, on)
            view.update()

    def _mode_changed(self) -> None:
        three_d = self.mode.currentData() == "3d"
        self.stack.setCurrentIndex(0 if three_d else 1)
        self.hint.setText("Drag to rotate, scroll to zoom." if three_d else "North is up.")
        self.legend.setText(
            "Blocks within 16 blocks of the agent that have a face open to air. The orange box is the agent, "
            "the pink line is its route with a box at the goal, and the white beam marks the current action's "
            "target. Overlays are drawn over the terrain so they stay visible behind hills."
            if three_d else
            "The top block of each column within 28 blocks. Routes are drawn flat, so height changes are not "
            "visible. The orange arrow is the agent; the white crosshair is the current action's target.")
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.timer.start()
        self.refresh()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.timer.stop()

    def _gone(self) -> None:
        self._spawned = False
        for view in self.views:
            view.clear()
        self.path_info.setText("No route has been planned yet.")

    def refresh(self) -> None:
        if not self._spawned or self._busy or not self.ctx.bridge.is_running():
            return
        self._busy = True
        if self.mode.currentData() == "3d":
            self.ctx.bridge.call("world.voxels", {"radius": RADIUS_3D}, self._have_voxels,
                                 lambda _e: setattr(self, "_busy", False), timeout_ms=8000)
        else:
            self.ctx.bridge.call("world.map", {"radius": RADIUS}, self._have_map,
                                 lambda _e: setattr(self, "_busy", False), timeout_ms=8000)

    def _have_voxels(self, result: dict) -> None:
        self._busy = False
        palette, blocks = result.get("palette"), result.get("blocks")
        if (isinstance(palette, list) and isinstance(blocks, list) and len(blocks) <= 9000
                and isinstance(result.get("center"), dict) and isinstance(result.get("position"), dict)
                and all(isinstance(b, list) and len(b) == 5 and all(isinstance(v, int) for v in b)
                        and 0 <= b[3] < len(palette) for b in blocks)):
            self.view3d.data = result
            self.view3d.update()

    def _have_map(self, result: dict) -> None:
        self._busy = False
        side = 2 * result.get("radius", 0) + 1
        if (isinstance(result.get("cells"), list) and len(result["cells"]) == side * side
                and isinstance(result.get("heights"), list) and len(result["heights"]) == side * side
                and isinstance(result.get("palette"), list) and isinstance(result.get("center"), dict)
                and all(isinstance(c, int) and -1 <= c < len(result["palette"]) for c in result["cells"])):
            self.view.map = result
            self.view.update()

    def _on_notification(self, method: str, params: dict) -> None:
        if method == "bot.status":
            self._spawned = params["phase"] == "spawned"
            if params["phase"] == "disconnected":
                self._gone()
        elif method == "path.update":
            status = params["status"]
            if status in ("arrived", "stopped"):
                for view in self.views:
                    view.path, view.explored = [], []
                self.path_info.setText("Arrived at the goal." if status == "arrived" else "Movement stopped.")
            else:
                for view in self.views:
                    view.path, view.explored = params["path"], params["explored"]
                words = {"success": "Route found", "partial": "Partial route (still searching)",
                         "noPath": "No route exists", "timeout": "Search timed out"}.get(status, status)
                self.path_info.setText(
                    f"{words}\n{len(params['path'])} steps, cost {params.get('cost', 0):.1f}\n"
                    f"A* examined {int(params.get('visited', 0)):,} positions and generated "
                    f"{int(params.get('generated', 0)):,} in {int(params.get('timeMs', 0))} ms"
                    + (f"\n(showing {len(params['explored']):,} of them)" if params["explored"] else ""))
            for view in self.views:
                view.update()

    def _on_event(self, e: dict) -> None:
        if e["kind"] == "tool_call":
            args = e["data"].get("arguments", {})
            if {"x", "y", "z"} <= set(args):
                self._mark(target=(math.floor(args["x"]), math.floor(args["y"]), math.floor(args["z"])))
        elif e["kind"] == "goal":
            self._mark(seen=[], target=None)

    def _mark(self, **changes) -> None:
        for view in self.views:
            for key, value in changes.items():
                setattr(view, key, value)
            view.update()

    def _on_observed(self, tool: str, args: dict, result: dict) -> None:
        if tool == "find_blocks":
            spots = result.get("positions") or []
            self._mark(seen=[(s["x"], s["y"], s["z"]) for s in spots if isinstance(s, dict) and {"x", "y", "z"} <= set(s)])
            self.seen_info.setText(f"find_blocks: {len(spots)} {result.get('block')} within {args.get('max_distance')} "
                                   "blocks (orange outlines).")
        elif tool == "scan_surroundings":
            blocks = [b for b in (result.get("blocks") or []) if isinstance(b, dict) and isinstance(b.get("nearest"), dict)]
            self._mark(seen=[(b["nearest"]["x"], b["nearest"]["y"], b["nearest"]["z"]) for b in blocks[:25]])
            top = ", ".join(f"{b['count']} {b['name']}" for b in blocks[:6])
            self.seen_info.setText(f"scan_surroundings (radius {result.get('radius')}): {len(blocks)} block types, "
                                   f"{len(result.get('entities') or [])} entities. Most common: {top}. Orange outlines "
                                   "mark the nearest block of each type.")
        elif tool in ("go_to", "mine_block", "place_block", "mine_blocks", "explore"):
            self._mark(target=None)
