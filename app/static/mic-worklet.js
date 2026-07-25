/**
 * Captures mic audio and posts it to the main thread in ~85ms batches.
 *
 * The audio graph hands us 128-sample frames (~5ms at 24kHz); forwarding each
 * one would mean ~187 WebSocket messages a second. Buffering to 2048 samples
 * keeps the stream responsive while cutting message volume by 16x.
 */
const BATCH_SAMPLES = 2048;

class MicCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buffer = new Float32Array(BATCH_SAMPLES);
    this._offset = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;

    for (let i = 0; i < channel.length; i++) {
      this._buffer[this._offset++] = channel[i];
      if (this._offset === BATCH_SAMPLES) {
        // Transfer a copy; the worklet reuses its own buffer next frame.
        this.port.postMessage(this._buffer.slice(0));
        this._offset = 0;
      }
    }
    return true;
  }
}

registerProcessor('mic-capture', MicCapture);
