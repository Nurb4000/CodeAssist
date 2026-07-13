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
  "caption": "{Title Suggestion 1} {Title Suggestion 2} Dense style description...",
  "lyrics": "[Verse 1]\nLyric line 1\nLyric line 2\n\n[Chorus]\nChorus line 1",
  "bpm": 120,
  "duration": 180,
  "keyscale": "C Major",
  "timesignature": "4/4",
  "vocal_language": "en"
}
```

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
   - BPM and key reference

### Caption Examples

```
{Midnight Drive} {Neon Highway} 80s synthwave, driving electronic beat, pulsing synth bass, atmospheric pads, male vocal with reverb, nostalgic retro feel, BPM 118, key of D minor
```

```
{Quiet Morning} {First Light} Intimate acoustic folk, fingerpicked guitar, soft female vocal, warm and breathy, minimal production, gentle and reflective, BPM 72, key of G major
```

---

## Lyrics Rules

Lyrics control how music unfolds over time using structure tags.

### Required Structure Tags

| Tag | Use For |
|-----|---------|
| `[Intro]` | Opening atmosphere (5-10 seconds) |
| `[Verse 1]` `[Verse 2]` | Narrative progression |
| `[Pre-Chorus]` | Build energy before chorus |
| `[Chorus]` | Emotional climax, repeated |
| `[Bridge]` | Transition or elevation |
| `[Outro]` | Ending, conclusion |
| `[Instrumental]` | No vocals, pure music |
| `[Guitar Solo]` `[Piano Interlude]` | Featured instrument |

### Vocal Style Tags (Optional Modifiers)

Use `-` to add style cues inside brackets:
```
[Chorus -anthemic, powerful]
[Verse 1 -whispered, intimate]
[Outro -spoken words, fading out]
```

### Critical Rules

1. **Lines outside brackets ARE SUNG** - No descriptions, only lyrics
2. **Use `[]` for ALL cues** - Never use `()` for stage directions
3. **6-10 syllables per line** - Model aligns syllables to beats
4. **Blank lines between sections** - Clear separation
5. **UPPERCASE = stronger intensity** - `WE ARE THE CHAMPIONS!`
6. **Parentheses = background vocals** - `We rise together (together)`

### Duration-to-Lyric Mapping

| Duration | Structure |
|----------|-----------|
| <60s | 1 verse + 1 chorus (6-10 lines) |
| 60-120s | 1-2 verses + 2 choruses |
| 120-180s | 2 verses + 2 choruses + bridge |
| >180s | 2-3 verses + 2-3 choruses + bridge + intro/outro |

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
[Intro -building atmosphere]
[Verse 1]
City lights are fading now
Shadows dance across the wall
Every silence tells a story
Every whisper starts to fall

[Pre-Chorus]
And I can feel the moment
Slipping through my hands

[Chorus -anthemic]
We are the ones who never sleep
Chasing dreams that run so deep
Light the fire, hold it high
We were born to touch the sky

[Verse 2]
Memories like photographs
Frozen in the golden hour
Every step a new beginning
Every fall another power

[Bridge -building intensity]
When the world goes quiet
And the stars align
We'll find our way back home
Every single time

[Chorus -anthemic]
We are the ones who never sleep
Chasing dreams that run so deep
Light the fire, hold it high
We were born to touch the sky

[Outro -fading, gentle]
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

### Duration Calculation

- Intro/Outro: 5-10 seconds each
- Instrumental sections: 5-15 seconds each
- Estimate longer rather than shorter

---

## Avoiding AI-Flavored Output

| Red Flag | Fix |
|----------|-----|
| Adjective stacking ("neon skies, electric hearts") | Be specific, use concrete imagery |
| Forced rhymes | Prioritize meaning over perfect rhyme |
| Mixed metaphors | One core metaphor per song |
| Lines too long to sing | Keep 6-10 syllables |

---

## Workflow

When user requests a song:

1. **Gather requirements** - Genre, mood, duration, theme
2. **Generate caption** - Start with title suggestions, add dense description
3. **Generate lyrics** - Follow duration-to-lyric mapping, use proper tags
4. **Set metadata** - Calculate duration from lyrics, choose BPM/key for genre
5. **Output JSON** - Complete payload ready for ACE-Step

### Example Request

User: "Create a melancholic indie folk song, about 2 minutes long"

### Example Output

```json
{
  "caption": "{Autumn Letters} {Paper Ghosts} Indie folk, fingerpicked acoustic guitar, soft male vocal, melancholic and intimate, warm analog recording, sparse arrangement with subtle strings, BPM 78, key of A minor",
  "lyrics": "[Intro]\n[Verse 1]\nLetters written never sent\nWords that time forgot to keep\nAutumn leaves on concrete steps\nPromises we couldn't keep\n\n[Chorus]\nWe were paper ghosts\nDrifting through the years\nHolding on to words\nThat disappeared\n\n[Verse 2]\nPhotographs in kitchen drawers\nFaces that we used to know\nEvery smile a distant shore\nEvery goodbye starts to show\n\n[Chorus]\nWe were paper ghosts\nDrifting through the years\nHolding on to words\nThat disappeared\n\n[Outro -fading]\nPaper ghosts...",
  "bpm": 78,
  "duration": 135,
  "keyscale": "A minor",
  "timesignature": "4/4",
  "vocal_language": "en"
}
```
