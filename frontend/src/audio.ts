export class Recorder {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private node: AudioWorkletNode | null = null;
  async start(onFrame: (frame: ArrayBuffer, level: number) => void) {
    if (!navigator.mediaDevices?.getUserMedia)
      throw new Error(
        "录音需要 localhost 或受信任的 HTTPS。请在主电脑本机打开页面。",
      );
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      this.context = new AudioContext({ sampleRate: 16000 });
      if (this.context.sampleRate !== 16000)
        throw new Error("浏览器不支持16kHz采集，请使用最新的 Chrome 或 Edge");
      await this.context.audioWorklet.addModule("/pcm-worklet.js");
      this.node = new AudioWorkletNode(this.context, "pcm-processor");
      this.source = this.context.createMediaStreamSource(this.stream);
      this.node.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        const samples = new Int16Array(event.data);
        let sum = 0;
        for (const v of samples) sum += (v / 32768) ** 2;
        onFrame(event.data, Math.sqrt(sum / samples.length));
      };
      this.source.connect(this.node);
      const mute = this.context.createGain();
      mute.gain.value = 0;
      this.node.connect(mute).connect(this.context.destination);
      await this.context.resume();
    } catch (e) {
      await this.stop();
      throw e;
    }
  }
  async stop() {
    this.node?.disconnect();
    this.source?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    if (this.context && this.context.state !== "closed")
      await this.context.close();
    this.context = null;
    this.stream = null;
    this.node = null;
    this.source = null;
  }
}
export function waveBlob(frames: ArrayBuffer[]): Blob {
  const size = frames.reduce((n, f) => n + f.byteLength, 0),
    header = new ArrayBuffer(44),
    v = new DataView(header);
  const str = (i: number, s: string) => {
    for (let j = 0; j < s.length; j++) v.setUint8(i + j, s.charCodeAt(j));
  };
  str(0, "RIFF");
  v.setUint32(4, 36 + size, true);
  str(8, "WAVE");
  str(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);
  v.setUint16(22, 1, true);
  v.setUint32(24, 16000, true);
  v.setUint32(28, 32000, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  str(36, "data");
  v.setUint32(40, size, true);
  return new Blob([header, ...frames], { type: "audio/wav" });
}
