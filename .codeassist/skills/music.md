---
name: music
description: Generate properly formatted song parameters (caption, lyrics, metadata) for ACE-Step music generation engine
slash: music
---

# Music Generation Skill

Generate structured song output compatible with the ACE-Step music generation engine.

## Output Format

Produce a JSON payload with the following structure:

```json
{
  "task_type": "text2music",
  "caption": "{Title Suggestion 1} {Title Suggestion 2} Dense style description...",
  "lyrics": "[Verse 1]\nLyric line 1\nLyric line 2\n\n[Chorus]\nChorus line 1",
  "bpm": 120,
  "duration": 180,
  "keyscale": "C Major",
  "timesignature": "4/4",
  "vocal_language": "en",
  "seed": 0
}
```

---

## Task Types

| Task | Description | When to Use |
|------|-------------|-------------|
| `text2music` | Generate music from text prompt | Default — new song creation |
| `cover` | Maintain structure, change style/lyrics | Remix, style transfer |
| `repaint` | Local modification using source audio context | Fix a section, change lyrics in place |
| `lego` | Add new tracks to existing audio | Add drums to guitar, etc. |
| `extract` | Separate tracks from mixed audio | Vocal isolation, stem separation |
| `complete` | Add accompaniment to single track | Add instruments to a cappella |

### Advanced Task Parameters

| Parameter | Use With | Description |
|-----------|----------|-------------|
| `src_audio` | cover, repaint, lego, extract, complete | Source audio file path |
| `reference_audio` | Any (global timbre/style control) | Reference audio for timbre/style |
| `audio_codes` | cover | Reuse semantic codes for variants |
| `audio_cover_strength` | cover | 0.0–1.0, higher = stricter structure adherence |
| `repainting_start` | repaint | Start time in seconds (3-90s range) |
| `repainting_end` | repaint | End time in seconds |

---

## Model Selection Guide

### LM (Language Model) — The Planner

| LM Choice | Speed | Knowledge | Best For |
|-----------|-------|-----------|----------|
| None | ⚡⚡⚡⚡ | — | You do the planning (Cover mode) |
| `0.6B` | ⚡⚡⚡ | Basic | Low VRAM (<8GB), rapid prototyping |
| `1.7B` | ⚡⚡ | Medium | **Default recommendation** |
| `4B` | ⚡ | Rich | Complex tasks, high-quality generation |

### DiT (Diffusion Transformer) — The Executor

| Model | Steps | CFG | Speed | Best For |
|-------|-------|-----|-------|----------|
| `turbo` | 8 | ❌ | ⚡⚡⚡ | **Daily use**, rapid iteration |
| `sft` | 50 | ✅ | ⚡ | Detail expression, CFG tuning |
| `base` | 50 | ✅ | ⚡ | extract, lego, complete tasks |
| `xl-turbo` | 8 | ❌ | ⚡⚡ | High quality daily use (≥12GB) |
| `xl-sft` | 50 | ✅ | ⚡ | Highest quality (≥12GB) |
| `xl-base` | 50 | ✅ | ⚡ | All tasks, highest quality (≥12GB) |

### Recommended Combinations

| Need | Combination |
|------|-------------|
| Daily use | `turbo` + `1.7B` |
| Fastest | `turbo` + No LM or `0.6B` |
| Best quality | `xl-turbo` or `xl-sft` + `1.7B` or `4B` |
| Special tasks | `base` model |
| Low VRAM | `turbo` + No LM + CPU offload |

---

## Caption Rules

Caption is the most important input. Format:

1. **Start with 2-3 title suggestions** wrapped in curly braces: `{Title One} {Another Title}`
2. **Follow with dense description** covering:
   - Genre/style (pop, rock, jazz, electronic, lo-fi, synthwave)
   - Emotion/atmosphere (melancholic, uplifting, energetic, dreamy)
   - Instruments (acoustic guitar, piano, synth pads, 808 drums)
   - Vocal characteristics (female vocal, male vocal, breathy, powerful)
   - Production style (lo-fi, studio-polished, bedroom pop)

### Caption Writing Principles

1. **Specific beats vague** — "sad piano ballad with female breathy vocal" works better than "a sad song"
2. **Combine multiple dimensions** — Style+emotion+instruments+timbre anchors direction precisely
3. **Use references well** — "in the style of 80s synthwave" or "reminiscent of Bon Iver"
4. **Texture words are useful** — warm, crisp, airy, punchy influence mixing and timbre
5. **Don't pursue perfection** — Caption is a starting point, iterate based on results
6. **Granularity determines freedom** — Less detail = more model creativity; more detail = more control
7. **Avoid conflicting styles** — "classical strings" + "hardcore metal" degrades output
   - **Repetition reinforcement** — Repeat elements you want more
   - **Conflict to evolution** — "Start with soft strings, middle becomes metal rock, end turns to hip-hop"
8. **Don't put BPM/key in Caption** — Use dedicated parameters instead

### Caption Examples

```
{Midnight Drive} {Neon Highway} 80s synthwave, driving electronic beat, pulsing synth bass, atmospheric pads, male vocal with reverb, nostalgic retro feel
```

```
{Quiet Morning} {First Light} Intimate acoustic folk, fingerpicked guitar, soft female vocal, warm and breathy, minimal production, gentle and reflective
```

---

## Lyrics Rules

Lyrics control how music unfolds over time using structure tags.

### Structure Tags

| Category | Tag | Description |
|----------|-----|-------------|
| **Basic** | `[Intro]` | Opening, establish atmosphere |
| | `[Verse]` `[Verse 1]` | Narrative progression |
| | `[Pre-Chorus]` | Build energy before chorus |
| | `[Chorus]` | Emotional climax, repeated |
| | `[Bridge]` | Transition or elevation |
| | `[Outro]` | Ending, conclusion |
| **Dynamic** | `[Build]` | Energy gradually rising |
| | `[Drop]` | Electronic music energy release |
| | `[Breakdown]` | Reduced instrumentation, space |
| **Instrumental** | `[Instrumental]` | Pure instrumental, no vocals |
| | `[Guitar Solo]` | Guitar solo |
| | `[Piano Interlude]` | Piano interlude |
| **Special** | `[Fade Out]` | Fade out ending |
| | `[Silence]` | Silence |

### Vocal Style Tags

| Tag | Effect |
|-----|--------|
| `[raspy vocal]` | Raspy, textured vocals |
| `[whispered]` | Whispered |
| `[falsetto]` | Falsetto |
| `[powerful belting]` | Powerful, high-pitched singing |
| `[spoken word]` | Rap/recitation |
| `[harmonies]` | Layered harmonies |
| `[call and response]` | Call and response |
| `[ad-lib]` | Improvised embellishments |

### Energy/Emotion Tags

| Tag | Effect |
|-----|--------|
| `[high energy]` | High energy, passionate |
| `[low energy]` | Low energy, restrained |
| `[building energy]` | Increasing energy |
| `[explosive]` | Explosive energy |
| `[melancholic]` | Melancholic |
| `[euphoric]` | Euphoric |
| `[dreamy]` | Dreamy |
| `[aggressive]` | Aggressive |

### Combining Tags

Use `-` for finer control, but keep it concise:
```
✅ [Chorus - anthemic]
❌ [Chorus - anthemic - stacked harmonies - high energy - powerful - epic]
```

### Caption-Lyrics Consistency

**Models are not good at resolving conflicts.** Checklist:
- Instruments in Caption ↔ Instrumental section tags in Lyrics
- Emotion in Caption ↔ Energy tags in Lyrics
- Vocal description in Caption ↔ Vocal control tags in Lyrics

### Critical Rules

1. **Lines outside brackets ARE SUNG** — No descriptions, only lyrics
2. **Use `[]` for ALL cues** — Never use `()` for stage directions
3. **6-10 syllables per line** — Model aligns syllables to beats
4. **Blank lines between sections** — Clear separation
5. **UPPERCASE = stronger intensity** — `WE ARE THE CHAMPIONS!`
6. **Parentheses = background vocals** — `We rise together (together)`
7. **Extend vowels cautiously** — `Feeeling so aliiive` (effects unstable)

### Duration-to-Lyric Mapping

| Duration | Structure |
|----------|-----------|
| <60s | 1 verse + 1 chorus (6-10 lines) |
| 60-120s | 1-2 verses + 2 choruses |
| 120-180s | 2 verses + 2 choruses + bridge |
| >180s | 2-3 verses + 2-3 choruses + bridge + intro/outro |

### Instrumental Music

```
[Instrumental]
```

Or describe instrumental development:
```
[Intro - ambient]
[Main Theme - piano]
[Climax - powerful]
[Outro - fade out]
```

### Lyrics Examples

**Short (60s):**
```
[Intro]
[Verse 1]
Walking through the morning rain
Every drop reminds me of you
[Chorus]
Hold on tight, we'll make it through
Everything will be alright
[Outro]
```

**Full (180s):**
```
[Intro - piano]

[Verse 1]
City lights are fading now
Shadows dance across the wall
Every silence tells a story
Every whisper starts to fall

[Pre-Chorus]
And I can feel the moment
Slipping through my hands

[Chorus - anthemic]
We are the ones who never sleep
Chasing dreams that run so deep
Light the fire, hold it high
We were born to touch the sky

[Verse 2]
Memories like photographs
Frozen in the golden hour
Every step a new beginning
Every fall another power

[Bridge - whispered]
When the world goes quiet
And the stars align
We'll find our way back home
Every single time

[Final Chorus]
We are the ones who never sleep
Chasing dreams that run so deep
Light the fire, hold it high
THIS IS OUR MOMENT!

[Outro - fade out]
Touch the sky...
```

---

## Metadata Rules

| Parameter | Range | Default | Notes |
|-----------|-------|---------|-------|
| `bpm` | 30-300 | 120 | Slow 60-80, mid 90-120, fast 130-180 |
| `duration` | seconds | 180 | Calculate from lyrics length |
| `keyscale` | key | C Major | C, G, D, Am, Em most stable |
| `timesignature` | sig | 4/4 | 3/4 for waltz, 6/8 for swing |
| `vocal_language` | lang | en | Auto-detected from lyrics |
| `seed` | int | 0 | Fixed = reproducible, 0 = random |

### Control Boundaries

- **BPM**: Common range (60–180) works well; extreme values have less training data
- **Key**: Common keys (C, G, D, Am, Em) are stable; rare keys may be ignored
- **Time signature**: `4/4` most reliable; `3/4`, `6/8` usually OK; complex signatures are advanced
- **Duration**: Short (30-60s) and medium (2-4min) stable; very long may have repetition issues

### Duration Calculation

- Intro/Outro: 5-10 seconds each
- Instrumental sections: 5-15 seconds each
- Estimate longer rather than shorter

---

## Inference Hyperparameters

### DiT Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `inference_steps` | 8 (turbo) | More steps = finer but slower |
| `guidance_scale` | 7.0 | CFG strength (Base/SFT only) |
| `shift` | 1.0 | Denoising trajectory; affects structure vs detail |
| `infer_method` | "ode" | `ode` = deterministic, `sde` = adds randomness |

**Shift explained:**
- Larger shift → stronger semantics, clearer framework ("draw outline first")
- Smaller shift → more details, but may include noise ("draw and fix simultaneously")

### LM Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `thinking` | true | Enable CoT reasoning for metadata/caption |
| `lm_temperature` | 0.85 | Higher = more creative, lower = more conservative |
| `lm_cfg_scale` | 2.0 | Higher = more prompt adherence |
| `lm_top_k` | 0 | 0 = disabled |
| `lm_top_p` | 0.9 | Nucleus sampling limit |
| `lm_negative_prompt` | "NO USER INPUT" | What to avoid generating |
| `use_cot_metas` | true | Let LM auto-infer BPM, key, etc. |
| `use_cot_caption` | true | Let LM optimize your caption |
| `use_cot_language` | true | Auto-detect vocal language |

---

## Audio Control

### Reference Audio (Global Timbre/Style)

Controls acoustic features: timbre, mixing style, performance style, atmosphere.

- Normalized to stereo 48kHz
- If <30 seconds, repeated to fill
- 10-second segments from front/middle/back concatenated into 30s reference

### Source Audio (Semantic Structure Control)

For Cover tasks — converts audio to semantic codes containing:
- Melody, rhythm, chords, orchestration
- Partial timbre information

Control strength with `audio_cover_strength` (0.0–1.0):
- Higher = stricter adherence to source structure
- Lower = more creative freedom

### Cover Mode Workflow

1. Input source audio
2. Optionally modify caption and lyrics
3. Model maintains melody structure, applies new style
4. Generate multiple versions with different strengths

### Repaint Mode Workflow

1. Input source audio context
2. Specify time interval (3-90 seconds)
3. Modify lyrics/structure in that interval
4. Model completes based on surrounding context

Use cases: fix a verse, change chorus lyrics, extend intro/outro, infinite duration generation

---

## Avoiding AI-Flavored Output

| Red Flag | Fix |
|----------|-----|
| Adjective stacking ("neon skies, electric hearts") | Be specific, use concrete imagery |
| Forced rhymes | Prioritize meaning over perfect rhyme |
| Mixed metaphors | One core metaphor per song |
| Lines too long to sing | Keep 6-10 syllables |
| Rhyme chaos | Consistent patterns, natural flow |
| Blurred section boundaries | Clear structure tag separation |
| No breathing room | Short lines, pause between sections |

**Metaphor discipline**: Stick to one core metaphor per song, exploring its multiple aspects. One image, multiple facets — gives lyrics cohesion.

---

## Batch Generation & Scoring

### Workflow

1. **Set batch_size** (2, 4, 8) to explore random space
2. **Fix seed** when tuning parameters to isolate effects
3. **Use auto-scoring** — DiT Lyrics Alignment Score helps screen versions
4. **Screen by score** then manually select best fit

### Seed Control

- `seed: 0` = random each generation
- `seed: <fixed>` = reproducible starting point
- Fix seed when testing parameter changes
- Vary seed when exploring creative space

---

## Workflow

When user requests a song:

1. **Gather requirements** — Genre, mood, duration, theme, task type
2. **Select models** — LM + DiT based on VRAM and quality needs
3. **Generate caption** — Title suggestions + dense description
4. **Generate lyrics** — Follow duration-to-lyric mapping, use proper tags
5. **Set metadata** — Calculate duration, choose BPM/key for genre
6. **Set hyperparameters** — Adjust shift, CFG, temperature as needed
7. **Output JSON** — Complete payload ready for ACE-Step

### Example Request

User: "Create a melancholic indie folk song, about 2 minutes long"

### Example Output

```json
{
  "task_type": "text2music",
  "caption": "{Autumn Letters} {Paper Ghosts} Indie folk, fingerpicked acoustic guitar, soft male vocal, melancholic and intimate, warm analog recording, sparse arrangement with subtle strings",
  "lyrics": "[Intro]\n[Verse 1]\nLetters written never sent\nWords that time forgot to keep\nAutumn leaves on concrete steps\nPromises we couldn't keep\n\n[Chorus]\nWe were paper ghosts\nDrifting through the years\nHolding on to words\nThat disappeared\n\n[Verse 2]\nPhotographs in kitchen drawers\nFaces that we used to know\nEvery smile a distant shore\nEvery goodbye starts to show\n\n[Chorus]\nWe were paper ghosts\nDrifting through the years\nHolding on to words\nThat disappeared\n\n[Outro -fading]\nPaper ghosts...",
  "bpm": 78,
  "duration": 135,
  "keyscale": "A minor",
  "timesignature": "4/4",
  "vocal_language": "en",
  "seed": 0
}
```
