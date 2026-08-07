# -*- coding: utf-8 -*-
"""animate_fleet.py —— 本地 CAV 编队动画演示生成器（成员 C）

读取 demo_platoon_edge.py 产出的数据，生成单文件 fleet_animation.html：
- 单路径模式：FRAMES（帧）+ TELE（遥测 CSV）
- 链式多路径模式（默认）：PATHS 数据包 {meta, paths:{1..K}}，动画页内**下拉选择边数**
  即时切换对应路径的黄色折线、frames 与遥测曲线

主图：完整园区路网 + 高亮折线 + 编队车（leader/跟随）逐帧行驶
控制条：播放/暂停 / 单步 / 倍速 / 时间轴滑块 / 当前 tick / 边数下拉（多路径）
遥测面板：速度、车-车间距、前车到目标节点距离 三条曲线（与播放头联动 markLine）

用法：
    cd 项目目录
    python animate_fleet.py --topology graph_data.yaml \
        --input data/platoon_multi.json --out platoon_animation.html     # 链式（多路径）
    python animate_fleet.py --topology graph_data.yaml \
        --input data/platoon_frames.json --telemetry data/platoon_telemetry.csv \
        --out platoon_animation.html                                    # 单路径

依赖：仅标准库 + pyyaml；ECharts 走 CDN（联网），联网成功时内联进 HTML（可离线打开）。
"""
import argparse
import csv
import json
import sys
import urllib.request
from pathlib import Path

import yaml

_ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"

_NODE_COLORS = {
    "entrance": "#FF4757", "road": "#2ED573", "admin": "#747D8C",
    "academic": "#1E90FF", "lab": "#70A1FF", "sports": "#FFA502",
    "living": "#FF6B81",
}

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CAV 编队行驶动画</title>
<style>
  :root { --bg:#0b0714; --panel:#130b21; --line:#2d1b4e; --ink:#e9d5ff; --accent:#a855f7; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:"Microsoft YaHei","PingFang SC",sans-serif;
         background:var(--bg); color:var(--ink); }
  .wrap { max-width:1200px; margin:0 auto; padding:16px; }
  h1 { font-size:20px; margin:0 0 4px; }
  .sub { color:#8b7aa8; font-size:13px; margin-bottom:12px; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:10px;
          padding:14px; margin-bottom:14px; }
  #mapChart { width:100%; height:560px; }
  .controls { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:10px; }
  .controls button { background:linear-gradient(90deg,#7c3aed,#9333ea); color:#fff;
                     border:none; border-radius:6px; padding:8px 18px; font-size:14px;
                     cursor:pointer; }
  .controls select {
      background:#1e1332; color:#c4b5fd; border:1px solid #3b2563;
      border-radius:6px; padding:6px 8px; font-size:13px; }
  .controls input[type=range] { flex:1; min-width:200px; accent-color:#a855f7; }
  .tick-label { font-size:15px; font-weight:bold; color:#f5a623; min-width:120px; }
  .telem { display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:14px; }
  .telem .chart { width:100%; height:220px; }
  .note { color:#8b7aa8; font-size:12px; margin-top:6px; }
  .msg { color:#f5a623; font-size:13px; padding:8px 0; }
</style>
</head>
<body>
<div class="wrap">
  <h1>🚗 CAV 编队行驶动画 · <span id="titleO"></span></h1>
  <div class="sub" id="subO"></div>

  <div class="card">
    <div id="mapChart"></div>
    <div class="controls">
      <button id="playBtn">⏸ 暂停</button>
      <button id="stepBtn">⏭ 单步</button>
      <span class="tick-label" id="tickLabel">tick -</span>
      <span id="edgeWrap" style="color:#8b7aa8;font-size:13px">边数
        <select id="edgeSel" style="display:none"></select>
      </span>
      <span style="color:#8b7aa8;font-size:13px">倍速</span>
      <select id="speedSel">
        <option value="0.5">0.5x</option>
        <option value="1" selected>1x</option>
        <option value="2">2x</option>
        <option value="5">5x</option>
        <option value="10">10x</option>
      </select>
      <input type="range" id="tickSlider" min="0" max="0" value="0">
    </div>
    <div class="note">黄色粗线 = 高亮路线 · 大圆点 = leader（CAV_L1，上方数字=已行驶距离 m）</div>
  </div>

  <div class="card">
    <div class="telem">
      <div><div class="chart" id="speedChart"></div><div class="note">速度 / (m·s⁻¹)</div></div>
      <div><div class="chart" id="gapChart"></div><div class="note">车-车间距 gap_to_front / m（leader=0）</div></div>
      <div><div class="chart" id="distChart"></div><div class="note">前车到目标节点距离 / m</div></div>
    </div>
  </div>
  <div id="errMsg" class="msg"></div>
</div>

__ECHARTS_SCRIPT__

<script>
const TOPO = __TOPO__;
const PATHS = __PATHS__;          // 链式多路径数据包或 null
const FRAMES = __FRAMES__;        // 单路径 {meta, frames}
const TELE = __TELE__;            // 单路径遥测

const nodePos = TOPO.nodePos;     // {id:[x,y]}
const edges = TOPO.edges;         // [[src,dst],...]

const isMulti = !!(PATHS && PATHS.paths);
let frames = isMulti ? [] : (FRAMES.frames || []);
let meta = isMulti ? (PATHS.meta || {}) : (FRAMES.meta || {});
let tele = isMulti ? {} : TELE;
let routeCoords = [];
let currentPathLen = 0;
let xMax = 0;                 // 底图 x 轴上限：收敛后再显示 30m 即停

let echartsLib = window.echarts;
const errEl = document.getElementById('errMsg');
if (!echartsLib) {
  errEl.textContent = '⚠️ 未检测到 ECharts（CDN 未加载，请联网后刷新本页）。';
}

// ---------- 主图（路网固定，route/车 随路径切换） ----------
function nodeTypeOf(id){ const n = TOPO.nodeTypes||{}; return n[id]||'road'; }
const graphNodes = Object.keys(nodePos).map(id => ({
  id, name: id, type: nodeTypeOf(id),
  value: [nodePos[id][0], nodePos[id][1]],
  symbol: 'circle', symbolSize: 7,
  itemStyle: { color: TOPO.nodeColors[nodeTypeOf(id)] || '#a855f7', opacity: 0.9 },
  label: { show: false }
}));
const graphLinks = edges.map(([s,t]) => ({
  source: s, target: t,
  lineStyle: { color: '#8a7fb5', width: 1, opacity: 0.45 }
}));
function buildRouteCoords(route_nodes) {
  return (route_nodes || []).map(id => nodePos[id] ? [nodePos[id][0], nodePos[id][1]] : null)
                            .filter(Boolean);
}

const mapChart = echartsLib ? echartsLib.init(document.getElementById('mapChart')) : null;
function baseOption() {
  return {
    backgroundColor: 'transparent',
    grid: { left: 10, right: 10, top: 10, bottom: 10 },
    xAxis: { type: 'value', min: 0, max: 100, show: false },
    yAxis: { type: 'value', min: 0, max: 100, show: false },
    tooltip: { trigger: 'item', formatter: p => {
        if (p.seriesType === 'effectScatter') {
          return '<b>' + p.data.name + '</b> (' + p.data.role + ')<br/>速度 ' +
                 p.data.speed + ' m/s<br/>车距 ' + p.data.dist + ' m<br/>到终点 ' +
                 p.data.toTarget + ' m';
        }
        if (p.seriesType === 'graph') return p.data.name;
        return '';
      } },
    series: [
      { type: 'graph', coordinateSystem: 'cartesian2d', layout: 'none',
        data: graphNodes, links: graphLinks, label: { show: false } },
      { id: 'route', name: 'route', type: 'lines', coordinateSystem: 'cartesian2d',
        polyline: true, data: [], label: { show: false } },
      { id: 'cars', name: 'cars', type: 'effectScatter', coordinateSystem: 'cartesian2d',
        data: [], zlevel: 3,
        rippleEffect: { scale: 2.5, brushType: 'stroke' },
        label: { show: false } }
    ]
  };
}
if (mapChart) mapChart.setOption(baseOption(), true);

function updateHeader() {
  const pf = (frames[0] || {}).path || {};
  const ts = frames.map(f => f.tick);
  document.getElementById('titleO').textContent =
    (pf.start_node_id || '-') + ' → ' + (pf.end_node_id || '-');
  document.getElementById('subO').textContent =
    '数据源: ' + (meta.source || 'platoon') + ' · ' + (meta.fleet_size || '-') +
    ' 车编队 · ' + frames.length + ' 帧（tick ' + (ts[0] ?? '-') + '→' +
    (ts[ts.length - 1] ?? '-') + '） · 路径总长 ' + currentPathLen + ' m';
}

// ---------- 遥测图表（tele 随路径切换） ----------
const PALETTE = ['#f5a623', '#31a354', '#2c7fb8', '#d64541'];
function colorById(id) {
  const i = Object.keys(tele).indexOf(id);
  return i >= 0 ? PALETTE[i % PALETTE.length] : '#f5a623';
}
function carSeries(dataKey) {
  const ids = Object.keys(tele);
  return ids.map(id => ({
    name: id, type: 'line', showSymbol: false, smooth: true,
    data: tele[id].tick.map((t, i) => [t, tele[id][dataKey][i]]),
    lineStyle: { width: 2, color: colorById(id) },
    itemStyle: { color: colorById(id) }
  }));
}
function teleChart(elId, dataKey) {
  const el = document.getElementById(elId);
  if (!echartsLib || !el) return null;
  const ch = echartsLib.init(el);
  ch._dkey = dataKey;
  ch.setOption({
    backgroundColor: 'transparent',
    grid: { left: 50, right: 16, top: 18, bottom: 24 },
    xAxis: { type: 'value', name: 'tick', axisLabel: { color: '#c4b5fd', fontSize: 10 } },
    yAxis: { type: 'value', axisLabel: { color: '#c4b5fd', fontSize: 10 }, scale: true },
    legend: { textStyle: { color: '#c4b5fd', fontSize: 11 }, top: 0 },
    tooltip: { trigger: 'axis' },
    series: carSeries(dataKey)
  });
  return ch;
}
const speedChart = teleChart('speedChart', 'speed');
const gapChart   = teleChart('gapChart', 'gap');
const distChart  = teleChart('distChart', 'dist');
function refreshTelemetry() {
  const keys = ['speed', 'gap', 'dist'];
  [speedChart, gapChart, distChart].forEach((ch, ix) => {
    if (ch) ch.setOption({ xAxis: { max: xMax }, series: carSeries(keys[ix]) });
  });
}
function setMarkLine(ch, tick) {
  if (!ch) return;
  const sers = carSeries(ch._dkey).map(s => ({
    name: s.name, type: 'line', data: s.data,
    markLine: { symbol: 'none', lineStyle: { color: '#f5a623', width: 1 },
                label: { show: false }, data: [{ xAxis: tick }] }
  }));
  ch.setOption({ series: sers });
}

// ---------- 播放控制 ----------
const slider = document.getElementById('tickSlider');
const tickLabel = document.getElementById('tickLabel');
const playBtn = document.getElementById('playBtn');
const stepBtn = document.getElementById('stepBtn');
const speedSel = document.getElementById('speedSel');
const edgeSel = document.getElementById('edgeSel');

let idx = 0, playing = true, timer = null;

function renderAt(i) {
  idx = Math.max(0, Math.min(i, frames.length - 1));
  slider.value = idx;
  const f = frames[idx];
  const tick = f ? f.tick : -1;
  tickLabel.textContent = 'tick ' + tick;

  const cars = (f && f.fleet ? f.fleet : []).map(c => ({
    name: c.car_id, role: c.role,
    speed: c.speed, dist: c.distance_to_front, toTarget: c.distance_to_target,
    mileage: c.mileage,
    value: [c.position.x, c.position.y],
    symbolSize: c.role === 'leader' ? 18 : 13,
    itemStyle: {
      color: c.role === 'leader' ? '#f5a623' : colorById(c.car_id),
      shadowBlur: 10, shadowColor: c.role === 'leader' ? '#f5a623' : '#a855f7'
    },
    // 仅 leader 上方显示已行驶距离（m），跟随车不显示标签
    label: c.role === 'leader' ? {
      show: true, position: 'top', color: '#fff', fontSize: 12, fontWeight: 'bold',
      textBorderColor: '#000', textBorderWidth: 2,
      formatter: p => Math.round(p.data.mileage) + ' m'
    } : { show: false }
  }));
  if (mapChart) mapChart.setOption({ series: [{ id: 'cars', data: cars }] });

  setMarkLine(speedChart, tick);
  setMarkLine(gapChart, tick);
  setMarkLine(distChart, tick);
}

function schedule() {
  if (timer) { clearInterval(timer); timer = null; }
  if (!playing) return;
  const sp = parseFloat(speedSel.value);
  timer = setInterval(() => {
    renderAt(idx + 1 >= frames.length ? 0 : idx + 1);
  }, Math.max(20, 100 / sp));
}

playBtn.addEventListener('click', () => {
  playing = !playing;
  playBtn.textContent = playing ? '⏸ 暂停' : '▶ 播放';
  schedule();
});
stepBtn.addEventListener('click', () => {
  playing = false;
  playBtn.textContent = '▶ 播放';
  schedule();
  renderAt((idx + 1) % frames.length);
});
speedSel.addEventListener('change', schedule);
slider.addEventListener('input', () => {
  playing = false;
  playBtn.textContent = '▶ 播放';
  schedule();
  renderAt(parseInt(slider.value, 10));
});
window.addEventListener('resize', () => {
  mapChart && mapChart.resize();
  speedChart && speedChart.resize();
  gapChart && gapChart.resize();
  distChart && distChart.resize();
});

// ---------- 路径加载（多路径切换） ----------
function loadPath(k) {
  const p = PATHS.paths[k];
  frames = p.frames || [];
  tele = p.telemetry || {};
  currentPathLen = p.total_path_length || 0;
  xMax = p.x_max || (frames.length ? Math.max(...frames.map(f => f.tick)) : 0);
  routeCoords = buildRouteCoords(p.route_nodes);
  if (mapChart) {
    mapChart.setOption({ series: [{ id: 'route', data: [{
      coords: routeCoords,
      lineStyle: { color: '#f5a623', width: 4, opacity: 0.9 }
    }]}]});
  }
  slider.max = Math.max(frames.length - 1, 0);
  idx = 0;
  updateHeader();
  refreshTelemetry();
  renderAt(0);
}

// ---------- 初始化 ----------
if (isMulti) {
  const keys = Object.keys(PATHS.paths).sort((a, b) => Number(a) - Number(b));
  keys.forEach(k => {
    const o = document.createElement('option');
    o.value = k;
    o.textContent = k + ' 条边';
    edgeSel.appendChild(o);
  });
  edgeSel.style.display = 'inline-block';
  const def = keys.indexOf('2') >= 0 ? '2' : keys[0];
  edgeSel.value = def;
  edgeSel.addEventListener('change', () => {
    playing = false;
    playBtn.textContent = '▶ 播放';
    schedule();
    loadPath(edgeSel.value);
  });
  loadPath(def);
} else {
  frames = FRAMES.frames || [];
  tele = TELE || {};
  meta = FRAMES.meta || {};
  xMax = meta.x_max || (frames.length ? Math.max(...frames.map(f => f.tick)) : 0);
  routeCoords = buildRouteCoords((frames[0] || {}).path ?
                                 frames[0].path.route_nodes : []);
  currentPathLen = (frames[0] || {}).total_path_length || 0;
  slider.max = Math.max(frames.length - 1, 0);
  updateHeader();
  refreshTelemetry();
  renderAt(0);
}
schedule();
</script>
</body>
</html>
"""


def _load_topology(path):
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    node_pos, node_types = {}, {}
    nodes = data.get("nodes", {})
    for nid, attrs in nodes.items():
        node_pos[nid] = [float(attrs["x"]), float(attrs["y"])]
        node_types[nid] = attrs.get("type", "road")
    edges = [(e["nodes"][0], e["nodes"][1]) for e in data.get("edges", [])
             if len(e.get("nodes", [])) == 2]
    return {"nodePos": node_pos, "nodeTypes": node_types,
            "nodeColors": _NODE_COLORS, "edges": edges}


def _load_telemetry(path):
    tele = {}
    with open(path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            cid = row["car_id"]
            item = tele.setdefault(cid, {"tick": [], "speed": [], "gap": [], "dist": []})
            item["tick"].append(float(row["tick"]))
            item["speed"].append(float(row["speed"]))
            item["gap"].append(float(row["gap_to_front"]))
            item["dist"].append(float(row["front_distance_to_target"]))
    return tele


def _fetch_echarts(timeout=8):
    """尝试下载 echarts.min.js 内联（离线可开）；失败返回 None 走 CDN。"""
    try:
        with urllib.request.urlopen(_ECHARTS_CDN, timeout=timeout) as r:
            return r.read().decode("utf-8")
    except Exception:
        return None


def main(argv=None):
    p = argparse.ArgumentParser(prog="animate_fleet",
                                description="CAV 编队动画演示生成器（成员 C）")
    p.add_argument("--input", default="data/platoon_multi.json")
    p.add_argument("--telemetry", default="data/platoon_telemetry.csv")
    p.add_argument("--topology", default="graph_data.yaml")
    p.add_argument("--out", default="platoon_animation.html")
    args = p.parse_args(argv)

    topo = _load_topology(args.topology)
    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    is_multi = isinstance(data, dict) and bool(data.get("paths"))
    if is_multi:
        paths_json = data
        frames_json = {}
        tele = {}
        n_frames = sum(len(p.get("frames", [])) for p in data["paths"].values())
        print(f"链式多路径数据包: {len(data['paths'])} 组（1~{len(data['paths'])} 边），"
              f"总帧 {n_frames}")
    else:
        paths_json = None
        frames_json = data
        tele = _load_telemetry(args.telemetry)
        if not frames_json.get("frames"):
            raise SystemExit("单路径帧文件为空，请先运行 demo_platoon_edge.py")
        print(f"单路径: {len(frames_json['frames'])} 帧 | 遥测车 {list(tele.keys())}")

    script = _fetch_echarts()
    if script:
        echarts_block = "<script>\n" + script + "\n</script>"
        print("ECharts 已内联（离线可打开）")
    else:
        echarts_block = ("<script src=\"" + _ECHARTS_CDN + "\"></script>")
        print("警告: 未能下载 ECharts，HTML 将走 CDN（需联网打开）")

    html = (_HTML_TEMPLATE
            .replace("__ECHARTS_SCRIPT__", echarts_block)
            .replace("__TOPO__", json.dumps(topo, ensure_ascii=False))
            .replace("__PATHS__", json.dumps(paths_json, ensure_ascii=False))
            .replace("__FRAMES__", json.dumps(frames_json, ensure_ascii=False))
            .replace("__TELE__", json.dumps(tele, ensure_ascii=False)))

    out = Path(args.out)
    out.write_text(html, encoding="utf-8")
    print(f"已生成 -> {out} ({out.stat().st_size} bytes)")
    print(f"  拓扑 {len(topo['nodePos'])} 节点 / {len(topo['edges'])} 边 | "
          f"模式 {'多路径（下拉选边数）' if is_multi else '单路径'}")
    print(f"  打开方式: 双击 {out.name} 或浏览器打开")
    return 0


if __name__ == "__main__":
    sys.exit(main())
