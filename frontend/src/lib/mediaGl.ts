// src/lib/mediaGl.ts
// Drawing the media layers with WebGL (0.4.0). A transparent video's preview
// copy holds its colour on top and its transparency below, as grey (backend
// media_assets.make_preview): H.264 has no alpha, and a <video> on screen
// draws partly transparent areas as opaque. So the shader takes the colour
// from the top half and the alpha from the bottom half's red. Opaque videos
// and images are drawn as they are (images uploaded premultiplied).
import type { MediaBox } from '@/types'

export interface MediaLayer {
  source: HTMLVideoElement | HTMLImageElement
  /** Images keep their texture under this key; a video's is uploaded every frame. */
  key: string
  stacked: boolean
  box: MediaBox
}

const VERTEX = `
attribute vec2 a_pos;
uniform vec4 u_box;
varying vec2 v_uv;
void main() {
  v_uv = a_pos;
  vec2 p = u_box.xy + a_pos * u_box.zw;
  gl_Position = vec4(p.x * 2.0 - 1.0, 1.0 - p.y * 2.0, 0.0, 1.0);
}`

// u_half: half a texel of the stacked frame's height, so neither half samples across the seam
const FRAGMENT = `
precision mediump float;
uniform sampler2D u_tex;
uniform bool u_stacked;
uniform float u_half;
varying vec2 v_uv;
void main() {
  if (u_stacked) {
    vec3 rgb = texture2D(u_tex, vec2(v_uv.x, min(v_uv.y * 0.5, 0.5 - u_half))).rgb;
    float a = texture2D(u_tex, vec2(v_uv.x, max(0.5 + v_uv.y * 0.5, 0.5 + u_half))).r;
    gl_FragColor = vec4(rgb * a, a);
  } else {
    gl_FragColor = texture2D(u_tex, v_uv);
  }
}`

export class MediaRenderer {
  private canvas: HTMLCanvasElement
  private gl: WebGLRenderingContext
  private program: WebGLProgram
  private uBox: WebGLUniformLocation
  private uStacked: WebGLUniformLocation
  private uHalf: WebGLUniformLocation
  private videoTexture: WebGLTexture
  private images = new Map<string, { texture: WebGLTexture; src: string }>()

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas
    const gl = canvas.getContext('webgl', { premultipliedAlpha: true, alpha: true, antialias: false })
    if (!gl) throw new Error('WebGL is not available')
    this.gl = gl
    const shader = (type: number, source: string) => {
      const s = gl.createShader(type)!
      gl.shaderSource(s, source)
      gl.compileShader(s)
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) ?? 'shader')
      return s
    }
    const program = gl.createProgram()!
    gl.attachShader(program, shader(gl.VERTEX_SHADER, VERTEX))
    gl.attachShader(program, shader(gl.FRAGMENT_SHADER, FRAGMENT))
    gl.linkProgram(program)
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program) ?? 'program')
    gl.useProgram(program)
    this.program = program
    const quad = gl.createBuffer()
    gl.bindBuffer(gl.ARRAY_BUFFER, quad)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1]), gl.STATIC_DRAW)
    const aPos = gl.getAttribLocation(program, 'a_pos')
    gl.enableVertexAttribArray(aPos)
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0)
    this.uBox = gl.getUniformLocation(program, 'u_box')!
    this.uStacked = gl.getUniformLocation(program, 'u_stacked')!
    this.uHalf = gl.getUniformLocation(program, 'u_half')!
    gl.uniform1i(gl.getUniformLocation(program, 'u_tex'), 0)
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true)
    gl.enable(gl.BLEND)
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA)
    this.videoTexture = this.texture()
  }

  private texture(): WebGLTexture {
    const gl = this.gl
    const t = gl.createTexture()!
    gl.bindTexture(gl.TEXTURE_2D, t)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
    return t
  }

  /** Clears the canvas and draws the layers in order (the first is the lowest). */
  draw(layers: MediaLayer[]): void {
    const gl = this.gl
    gl.viewport(0, 0, this.canvas.width, this.canvas.height)
    gl.clearColor(0, 0, 0, 0)
    gl.clear(gl.COLOR_BUFFER_BIT)
    for (const layer of layers) {
      const { source, box } = layer
      let height: number
      if (source instanceof HTMLVideoElement) {
        if (source.readyState < 2 || !source.videoWidth) continue
        gl.bindTexture(gl.TEXTURE_2D, this.videoTexture)
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source)
        height = source.videoHeight
      } else {
        if (!source.complete || !source.naturalWidth) continue
        let cached = this.images.get(layer.key)
        if (!cached || cached.src !== source.src) {
          cached = { texture: cached?.texture ?? this.texture(), src: source.src }
          gl.bindTexture(gl.TEXTURE_2D, cached.texture)
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source)
          this.images.set(layer.key, cached)
        }
        gl.bindTexture(gl.TEXTURE_2D, cached.texture)
        height = source.naturalHeight
      }
      gl.uniform4f(this.uBox, box.x, box.y, box.width, box.height)
      gl.uniform1i(this.uStacked, layer.stacked ? 1 : 0)
      gl.uniform1f(this.uHalf, 0.5 / Math.max(1, height))
      gl.drawArrays(gl.TRIANGLES, 0, 6)
    }
  }

  dispose(): void {
    const gl = this.gl
    for (const { texture } of this.images.values()) gl.deleteTexture(texture)
    gl.deleteTexture(this.videoTexture)
    gl.deleteProgram(this.program)
    this.images.clear()
  }
}
