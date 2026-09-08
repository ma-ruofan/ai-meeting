// The AudioContext is explicitly 16 kHz. Buffer 100 ms; never transfer native-rate samples as 16 kHz.
class PCMProcessor extends AudioWorkletProcessor {
  constructor() { super(); this.buffer = new Float32Array(1600); this.position = 0; }
  process(inputs) {
    const input = inputs[0]?.[0];
    if (input) for (const sample of input) {
      this.buffer[this.position++] = sample;
      if (this.position === this.buffer.length) {
        const pcm = new Int16Array(this.buffer.length);
        for (let i=0;i<pcm.length;i++) pcm[i] = Math.max(-1,Math.min(1,this.buffer[i])) * 32767;
        this.port.postMessage(pcm.buffer, [pcm.buffer]);
        this.position = 0;
      }
    }
    return true;
  }
}
registerProcessor('pcm-processor', PCMProcessor);
