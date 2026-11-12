import { useEffect, useMemo, useRef } from "react";
import type { ScienceBuffer } from "../../api/client";
import type { ColormapMode, ImageOp, StretchMode } from "../state/viewStore";

interface WebGL2SkyCanvasProps {
  frameA?: ScienceBuffer;
  frameB?: ScienceBuffer;
  fallbackUrl?: string;
  stretch: StretchMode;
  op: ImageOp;
  colormap: ColormapMode;
  swipe: number;
  label: string;
  onVisibilityPause?: (paused: boolean) => void;
}

const VERT_SRC = `#version 300 es
in vec2 a_pos;
out vec2 v_uv;
void main() {
  v_uv = a_pos * 0.5 + 0.5;
  v_uv.y = 1.0 - v_uv.y;
  gl_Position = vec4(a_pos, 0.0, 1.0);
}`;

const FRAG_SRC = `#version 300 es
precision highp float;
uniform sampler2D u_texA;
uniform sampler2D u_texB;
uniform float u_vmin;
uniform float u_vmax;
uniform int u_op;
uniform int u_stretch;
uniform int u_cmap;
uniform float u_swipe;
in vec2 v_uv;
out vec4 outColor;

float stretchVal(float v, int kind) {
  float x = clamp((v - u_vmin) / max(1e-6, u_vmax - u_vmin), 0.0, 1.0);
  if (kind == 1) return log(1.0 + 100.0 * x) / log(101.0);
  if (kind == 2) return sqrt(x);
  if (kind == 3) return x;
  return asinh(6.0 * x) / asinh(6.0);
}

vec3 cmapColor(float t, int cmap) {
  if (cmap == 1) return vec3(pow(t, 0.85), pow(t, 1.15) * 0.86, pow(t, 1.55) * 0.68);
  if (cmap == 2) return vec3(0.26 + 0.72 * t * t, 0.14 + 0.78 * t, 0.38 + 0.45 * sin(t * 2.4));
  return vec3(t);
}

void main() {
  float a = texture(u_texA, v_uv).r;
  float b = texture(u_texB, v_uv).r;
  if (u_op == 2) { // A - B bipolar residual
    float span = max(1e-5, (u_vmax - u_vmin) * 0.55);
    float d = (a - b) / span;
    vec3 neg = vec3(0.18, 0.49, 0.65) * clamp(-d, 0.0, 1.0);
    vec3 pos = vec3(0.84, 0.46, 0.25) * clamp(d, 0.0, 1.0);
    outColor = vec4(vec3(0.08, 0.08, 0.07) + neg + pos, 1.0);
    return;
  }
  float val = a;
  if (u_op == 1) val = 0.5 * (a + b);
  else if (u_op == 3) val = u_vmin + clamp((a / max(1e-3, b)) - 0.5, 0.0, 2.0) * 0.5 * (u_vmax - u_vmin);
  else if (u_op == 4) val = v_uv.x < u_swipe ? a : b;
  float t = stretchVal(val, u_stretch);
  vec3 col = cmapColor(t, u_cmap);
  if (u_op == 4 && abs(v_uv.x - u_swipe) < 0.004) col = vec3(0.95, 0.75, 0.35);
  outColor = vec4(col, 1.0);
}`;

function computePercentileRange(buf?: Float32Array): [number, number] {
  if (!buf?.length) return [0, 1];
  const sample: number[] = [];
  const step = Math.max(1, Math.floor(buf.length / 2048));
  for (let i = 0; i < buf.length; i += step) {
    const val = buf[i];
    if (val !== undefined && Number.isFinite(val)) sample.push(val);
  }
  sample.sort((x, y) => x - y);
  const lo = sample[Math.floor(sample.length * 0.02)] ?? 0;
  const hi = sample[Math.floor(sample.length * 0.995)] ?? lo + 1;
  return [lo, hi > lo ? hi : lo + 1];
}

const OP_CODE: Record<ImageOp, number> = { none: 0, sum: 1, diff: 2, ratio: 3, swipe: 4 };
const STRETCH_CODE: Record<StretchMode, number> = { asinh: 0, log: 1, sqrt: 2, linear: 3 };
const CMAP_CODE: Record<ColormapMode, number> = { gray: 0, sepia: 1, viridis: 2 };

/** Float32 WebGL2 scientific image renderer with 2D raster fallback and off-screen pause. */
export function WebGL2SkyCanvas({
  frameA,
  frameB,
  fallbackUrl,
  stretch,
  op,
  colormap,
  swipe,
  label,
  onVisibilityPause,
}: WebGL2SkyCanvasProps) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const glState = useRef<{
    gl: WebGL2RenderingContext;
    prog: WebGLProgram;
    texA: WebGLTexture;
    texB: WebGLTexture;
  } | null>(null);

  const [vmin, vmax] = useMemo(() => computePercentileRange(frameA?.data), [frameA]);

  // Pause blink loop when container is off-screen (contentvisibilityautostatechange + IO fallback)
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || !onVisibilityPause) return;
    if ("contentVisibility" in document.documentElement.style) {
      const onVis = (e: Event) =>
        onVisibilityPause(Boolean((e as Event & { skipped?: boolean }).skipped));
      el.addEventListener("contentvisibilityautostatechange", onVis);
      return () => el.removeEventListener("contentvisibilityautostatechange", onVis);
    }
    const io = new IntersectionObserver(
      (entries) => onVisibilityPause(!entries[0]?.isIntersecting),
      { rootMargin: "200px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [onVisibilityPause]);

  // Render via WebGL2 R32F shader, or deterministic 2D Canvas fallback if WebGL2 is absent
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !frameA) return;
    const { width, height, data } = frameA;
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }

    if (!glState.current) {
      const gl = canvas.getContext("webgl2", { antialias: false, preserveDrawingBuffer: true });
      if (gl) {
        const compile = (type: number, src: string) => {
          const sh = gl.createShader(type)!;
          gl.shaderSource(sh, src);
          gl.compileShader(sh);
          return sh;
        };
        const prog = gl.createProgram()!;
        gl.attachShader(prog, compile(gl.VERTEX_SHADER, VERT_SRC));
        gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FRAG_SRC));
        gl.linkProgram(prog);
        const buf = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buf);
        gl.bufferData(
          gl.ARRAY_BUFFER,
          new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]),
          gl.STATIC_DRAW,
        );
        const loc = gl.getAttribLocation(prog, "a_pos");
        gl.enableVertexAttribArray(loc);
        gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

        const mkTex = () => {
          const t = gl.createTexture()!;
          gl.bindTexture(gl.TEXTURE_2D, t);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
          return t;
        };
        glState.current = { gl, prog, texA: mkTex(), texB: mkTex() };
      }
    }

    const st = glState.current;
    const bData = frameB?.data.length === data.length ? frameB.data : data;

    if (st) {
      const { gl, prog, texA, texB } = st;
      gl.viewport(0, 0, width, height);
      gl.useProgram(prog);
      const upload = (tex: WebGLTexture, unit: number, pixels: Float32Array) => {
        gl.activeTexture(gl.TEXTURE0 + unit);
        gl.bindTexture(gl.TEXTURE_2D, tex);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.R32F, width, height, 0, gl.RED, gl.FLOAT, pixels);
      };
      upload(texA, 0, data);
      upload(texB, 1, bData);
      gl.uniform1i(gl.getUniformLocation(prog, "u_texA"), 0);
      gl.uniform1i(gl.getUniformLocation(prog, "u_texB"), 1);
      gl.uniform1f(gl.getUniformLocation(prog, "u_vmin"), vmin);
      gl.uniform1f(gl.getUniformLocation(prog, "u_vmax"), vmax);
      gl.uniform1i(gl.getUniformLocation(prog, "u_op"), OP_CODE[op]);
      gl.uniform1i(gl.getUniformLocation(prog, "u_stretch"), STRETCH_CODE[stretch]);
      gl.uniform1i(gl.getUniformLocation(prog, "u_cmap"), CMAP_CODE[colormap]);
      gl.uniform1f(gl.getUniformLocation(prog, "u_swipe"), swipe);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      return;
    }

    // 2D fallback for software/headless environments without WebGL2
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const img = ctx.createImageData(width, height);
    const span = Math.max(1e-6, vmax - vmin);
    for (let i = 0; i < data.length; i++) {
      const a = data[i] ?? 0;
      const b = bData[i] ?? a;
      let g = Math.max(
        0,
        Math.min(1, Math.asinh(6 * Math.max(0, (a - vmin) / span)) / Math.asinh(6)),
      );
      if (op === "diff") g = Math.max(0, Math.min(1, 0.5 + (a - b) / span));
      else if (op === "sum") g = Math.max(0, Math.min(1, (0.5 * (a + b) - vmin) / span));
      else if (op === "ratio") g = Math.max(0, Math.min(1, a / Math.max(1e-3, b) - 0.5));
      const p = i * 4;
      const byte = Math.round(g * 255);
      img.data[p] = byte;
      img.data[p + 1] = colormap === "sepia" ? Math.round(byte * 0.86) : byte;
      img.data[p + 2] = colormap === "sepia" ? Math.round(byte * 0.68) : byte;
      img.data[p + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
  }, [frameA, frameB, vmin, vmax, stretch, op, colormap, swipe]);

  return (
    <div ref={wrapRef} className="viewer-gl-wrap">
      {!frameA && fallbackUrl && (
        <img src={fallbackUrl} alt={label} className="viewer-fallback-img" />
      )}
      <canvas ref={canvasRef} className="viewer-gl-canvas" role="img" aria-label={label} />
    </div>
  );
}
