// WebGL is progressive enhancement only (#698): the turntable is the default
// and must work on GPU-less and RDP sessions that never get a WebGL context.

export function webgl2Available(doc = globalThis.document) {
  try {
    const canvas = doc.createElement("canvas");
    const gl = canvas.getContext("webgl2");
    if (!gl) return false;
    const usable = !gl.isContextLost();
    gl.getExtension("WEBGL_lose_context")?.loseContext();
    return usable;
  } catch {
    return false;
  }
}
