#!/usr/bin/env node
// 묶음마다 음성을 한 번 받아 무음 경계에서 자른다. 대본 원문은 바꾸지 않는다.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { execFileSync, spawnSync } from 'node:child_process';

class VideoError extends Error {}

const HERE = path.dirname(fileURLToPath(import.meta.url));
const VOICES = {
  gemini: { provider: 'gemini', id: 'Sulafat', style: 'A warm, friendly Korean woman casually showing a web app to a friend. Natural conversational polite Korean (존댓말), relaxed medium pace, not salesy, not robotic. Leave a clear short pause between sentences.' },
  edge: { provider: 'edge', id: 'ko-KR-SunHiNeural', rate: '+0%' },
};

function options() {
  const opt = { provider: 'gemini', only: null, out: path.join(HERE, 'out/voice') };
  const args = process.argv.slice(2);
  for (let k = 0; k < args.length; k++) {
    const flag = args[k];
    if (flag === '--help' || flag === '-h') {
      console.log('사용법: node scripts/video/voice.mjs [--provider gemini|edge] [--only 0~3] [--out 음성폴더]');
      process.exit(0);
    }
    if (!['--provider', '--only', '--out'].includes(flag)) throw new VideoError('알 수 없는 옵션입니다. --help를 확인하세요.');
    const value = args[++k];
    if (!value || value.startsWith('--')) throw new VideoError('옵션 뒤에 값을 지정하세요.');
    opt[flag.slice(2)] = value;
  }
  if (!VOICES[opt.provider]) throw new VideoError('음성 공급자는 gemini 또는 edge로 지정하세요.');
  if (opt.only !== null) {
    if (!/^\d+$/.test(opt.only)) throw new VideoError('묶음 번호는 0부터 시작하는 정수입니다.');
    opt.only = Number(opt.only);
  }
  opt.out = path.resolve(opt.out.replace(/^~(?=\/)/, os.homedir()));
  return opt;
}

function detectSilence(file) {
  const r = spawnSync('ffmpeg', ['-hide_banner', '-i', file, '-af', 'silencedetect=noise=-40dB:d=0.12', '-f', 'null', '-'], { encoding: 'utf8' });
  if (r.error || r.status !== 0) throw new VideoError('ffmpeg 무음 검출에 실패했습니다.');
  const pauses = [];
  let start = null;
  for (const line of r.stderr.split('\n')) {
    const a = line.match(/silence_start: ([\d.]+)/);
    if (a) start = Number(a[1]);
    const b = line.match(/silence_end: ([\d.]+)/);
    if (b && start !== null) {
      const end = Number(b[1]);
      pauses.push({ s: start, e: end, d: end - start });
      start = null;
    }
  }
  return pauses;
}

// 글자 수에 따른 예상 길이와 쉼의 길이로 문장 경계를 동적 계획한다.
function cut(total, pauses, lengths) {
  const n = lengths.length;
  if (n === 1) return [];
  const cand = pauses.filter(p => p.d >= 0.15 && p.s > 0.05 && p.e < total - 0.05);
  if (cand.length < n - 1) throw new VideoError('문장 사이 무음이 부족합니다. 원본 음성을 확인하세요.');
  const sum = lengths.reduce((a, b) => a + b, 0);
  const rate = sum / Math.max(total * 0.5, total - 0.5 * (n - 1));
  const cost = (k, a, b) => b <= a ? Infinity : ((b - a - lengths[k] / rate) / (lengths[k] / rate)) ** 2 * 6;
  const dp = Array.from({ length: n - 1 }, () => Array(cand.length).fill(Infinity));
  const from = dp.map(row => row.map(() => -1));
  for (let j = 0; j < cand.length; j++) dp[0][j] = cost(0, 0, cand[j].s) - Math.min(cand[j].d, 0.8) * 2;
  for (let k = 1; k < n - 1; k++) for (let j = 0; j < cand.length; j++) for (let p = 0; p < j; p++) {
    const c = dp[k - 1][p] + cost(k, cand[p].e, cand[j].s) - Math.min(cand[j].d, 0.8) * 2;
    if (c < dp[k][j]) { dp[k][j] = c; from[k][j] = p; }
  }
  let best = Infinity, last = -1;
  for (let j = 0; j < cand.length; j++) {
    const c = dp[n - 2][j] + cost(n - 1, cand[j].e, total);
    if (c < best) { best = c; last = j; }
  }
  if (last < 0) throw new VideoError('문장 경계를 찾지 못했습니다. 원본 음성을 확인하세요.');
  const cuts = [];
  for (let k = n - 2, j = last; k >= 0; j = from[k][j], k--) cuts.unshift(cand[j]);
  return cuts;
}

async function main() {
  const opt = options();
  const script = JSON.parse(fs.readFileSync(path.join(HERE, 'scenes.json'), 'utf8'));
  if (opt.only !== null && opt.only >= script.groups.length) throw new VideoError('존재하지 않는 묶음 번호입니다.');
  const module = path.join(os.homedir(), '.claude/skills/narrated-video/scripts/tts.mjs');
  const { speak, loadEnv, dur } = await import(pathToFileURL(module).href);
  loadEnv('~/.config/belorn/voice.env');
  // 공급자 오류의 본문에도 키가 섞이지 않도록 공용 모듈의 재시도 로그까지 가린다.
  const secrets = Object.entries(process.env).filter(([k, v]) => /KEY|TOKEN|SECRET/.test(k) && v).map(([, v]) => v);
  const errorLog = console.error.bind(console);
  console.error = (...args) => errorLog(...args.map(arg => {
    let text = String(arg);
    for (const secret of secrets) text = text.split(secret).join('[비공개]');
    return text;
  }));
  if (opt.provider === 'gemini' && !process.env.GEMINI_API_KEY) throw new VideoError('Gemini 키가 없습니다. 환경 변수 또는 ~/.config/belorn/voice.env에 설정하세요.');
  fs.mkdirSync(path.join(opt.out, 'raw'), { recursive: true });
  const manifest = path.join(opt.out, 'lines.json');
  const expected = script.scenes.flatMap(s => s.lines.map((t, i) => ({ scene: s.key, i, t })));
  let all = fs.existsSync(manifest) ? JSON.parse(fs.readFileSync(manifest, 'utf8')) : [];
  all = all.filter(l => expected.some(e => e.scene === l.scene && e.i === l.i && e.t === l.t));
  for (const [gi, keys] of script.groups.entries()) {
    if (opt.only !== null && gi !== opt.only) continue;
    const lines = keys.flatMap(key => expected.filter(l => l.scene === key));
    const spoken = lines.map(l => {
      let text = l.t;
      for (const [before, after] of Object.entries(script.speak)) text = text.split(before).join(after);
      return text;
    });
    console.log(`묶음 ${gi}: ${lines.length}문장 (${opt.provider})`);
    let audio;
    try { audio = await speak(VOICES[opt.provider], spoken.join('\n'), path.join(opt.out, 'raw'), `g${gi}`); }
    catch (e) { throw new VideoError(`묶음 ${gi} 음성 생성 실패${e.status ? ` (HTTP ${e.status})` : ''}${e.daily ? ': 하루 한도에 도달했습니다.' : '. 공급자 설정과 연결을 확인하세요.'}`); }
    if (!Number.isFinite(audio.dur) || audio.dur <= 0) throw new VideoError('원본 음성 길이가 올바르지 않습니다.');
    const lengths = spoken.map(t => Math.max(1, t.replace(/[\s,.?!]/g, '').length));
    const cuts = cut(audio.dur, detectSilence(audio.file), lengths);
    const bounds = [0, ...cuts.map(p => (p.s + p.e) / 2), audio.dur];
    const records = [];
    for (const [k, l] of lines.entries()) {
      const dst = path.join(opt.out, `${l.scene}-${l.i}.wav`);
      const tmp = dst.replace(/\.wav$/, '.tmp.wav');
      execFileSync('ffmpeg', ['-v', 'error', '-y', '-i', audio.file, '-ss', bounds[k].toFixed(6), '-to', bounds[k + 1].toFixed(6), '-af',
        'silenceremove=start_periods=1:start_threshold=-45dB,areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse,afade=t=in:d=0.01',
        '-ar', '48000', '-ac', '1', tmp], { stdio: ['ignore', 'pipe', 'pipe'] });
      const duration = dur(tmp);
      if (!Number.isFinite(duration) || duration <= 0) throw new VideoError('잘라 낸 문장 음성이 비었습니다.');
      fs.renameSync(tmp, dst);
      records.push({ ...l, file: dst, dur: duration, provider: opt.provider, voice: VOICES[opt.provider].id });
      const rate = lengths[k] / duration;
      console.log(`  ${l.scene}-${l.i}: ${duration.toFixed(2)}초${rate < 3.5 || rate > 9 ? ' (읽기 속도 확인 필요)' : ''}`);
    }
    all = [...all.filter(l => !keys.includes(l.scene)), ...records];
    all.sort((a, b) => expected.findIndex(e => e.scene === a.scene && e.i === a.i) - expected.findIndex(e => e.scene === b.scene && e.i === b.i));
    fs.writeFileSync(manifest + '.tmp', JSON.stringify(all, null, 2) + '\n');
    fs.renameSync(manifest + '.tmp', manifest);
  }
  console.log(`음성 ${all.length}문장, 합계 ${all.reduce((s, l) => s + l.dur, 0).toFixed(1)}초 → ${manifest}`);
}

main().catch(error => {
  // 외부 모듈·명령의 오류 객체는 키나 응답 본문을 포함할 수 있으므로 출력하지 않는다.
  console.error('오류: ' + (error instanceof VideoError ? error.message : '음성을 준비하지 못했습니다. 공용 TTS 모듈·키 설정·ffmpeg를 확인하세요. (--help)'));
  process.exitCode = 1;
});
