---
name: imagegen
description: Generate structured prompts for Stable Diffusion image generation with proper syntax, weights, and negative prompts
slash: imagegen
---

# Stable Diffusion Prompt Generator

Generate properly formatted prompts for Stable Diffusion image generation.

## Output Format

Always output two fields:

```
POSITIVE PROMPT:
[Your positive prompt here]

NEGATIVE PROMPT:
[Your negative prompt here]

RECOMMENDED SETTINGS:
CFG Scale: 7
Sampler: DPM++ 2M Karras
Steps: 20-30
```

---

## Prompt Syntax Rules

### Token Weighting

| Syntax | Effect | Use Case |
|--------|--------|----------|
| `(word)` | 1.1x weight | Slight emphasis |
| `((word))` | 1.21x weight | Moderate emphasis |
| `(((word)))` | 1.33x weight | Strong emphasis |
| `(word:1.5)` | Precise 1.5x | Fine control |
| `[word]` | 0.9x weight | De-emphasize |

**Rules:**
- Use weighting sparingly — stacking too many boosts causes artifacts
- Keep weights between 0.5 and 1.5 for most cases
- Values above 2.0 often produce distorted results

### BREAK Keyword

Use `BREAK` (uppercase) to separate prompt chunks beyond 75 tokens:
```
detailed landscape, mountains, sunset, dramatic clouds BREAK ancient castle, stone walls, ivy covered, medieval architecture BREAK cinematic lighting, golden hour, volumetric rays
```

### Prompt Structure

```
[Quality tags], [Subject], [Details], [Style], [Lighting], [Color]
```

**Order matters** — SD focuses more on keywords at the start.

---

## Positive Prompt Template

### Universal Quality Tags (Start of Prompt)

```
masterpiece, best quality, ultra detailed, intricate detail, high resolution, 8K
```

### Subject Categories

| Category | Examples |
|----------|----------|
| **People** | `1girl`, `1boy`, `portrait`, `full body`, `group` |
| **Animals** | `cat`, `dog`, `dragon`, `phoenix`, `wolf` |
| **Landscape** | `mountain landscape`, `cityscape`, `underwater`, `space` |
| **Objects** | `sword`, `car`, `house`, `flower`, `crystal` |

### Style Keywords

| Style | Keywords |
|-------|----------|
| **Photorealistic** | `photorealistic`, `photography`, `DSLR`, `film grain`, `raw photo` |
| **Digital Art** | `digital art`, `digital painting`, `concept art`, `artstation` |
| **Anime** | `anime style`, `manga`, `cel shading`, `vibrant colors` |
| **Oil Painting** | `oil painting`, `canvas texture`, `brushstrokes`, `classical art` |
| **Watercolor** | `watercolor`, `soft edges`, `paper texture`, `gentle gradients` |
| **3D Render** | `3D render`, `octane render`, `unreal engine 5`, `Cinema 4D` |
| **Pixel Art** | `pixel art`, `16-bit`, `retro gaming`, `sprite` |
| **Sketch** | `pencil sketch`, `charcoal drawing`, `line art`, `detailed linework` |

### Lighting Keywords

| Lighting | Effect |
|----------|--------|
| `golden hour` | Warm, soft sunset light |
| `blue hour` | Cool twilight tones |
| `dramatic lighting` | High contrast, cinematic |
| `soft studio lighting` | Even, professional |
| `neon lights` | Cyberpunk, vibrant |
| `volumetric lighting` | God rays, atmospheric |
| `rim lighting` | Backlit edge highlight |
| `chiaroscuro` | Strong light/dark contrast |

### Quality Boosters

```
sharp focus, highly detailed, professional, award-winning, stunning, beautiful, elegant, intricate
```

---

## Negative Prompt Templates

### Universal Negative (Use for All)

```
ugly, deformed, mutation, extra limbs, blurry, oversaturated, watermark, text, logo, poorly drawn hands, bad anatomy, artifacts, low quality, worst quality, jpeg artifacts, signature
```

### Photorealistic Negative

```
cartoon, anime, drawing, painting, illustration, 3D render, CGI, artificial, plastic skin, uncanny valley
```

### Anime Negative

```
photorealistic, photograph, 3D render, bad anatomy, extra fingers, missing fingers, fused fingers, too many fingers, bad hands
```

### Portrait Negative

```
deformed face, asymmetrical face, cross-eyed, open mouth (if unwanted), double image, cropped head
```

### Landscape Negative

```
people, person, figure, text, watermark, signature, blurry background (if sharp needed)
```

---

## Style Examples

### Photorealistic Portrait

```
POSITIVE:
masterpiece, best quality, photorealistic, portrait of a young woman, soft studio lighting, sharp focus, 8K, Canon EOS R5, natural expression, detailed skin texture, shallow depth of field

NEGATIVE:
cartoon, anime, painting, illustration, deformed face, bad anatomy, blurry, low quality, watermark
```

### Fantasy Landscape

```
POSITIVE:
masterpiece, best quality, digital painting, epic fantasy landscape, floating islands, crystal waterfalls, bioluminescent forest, dramatic clouds, volumetric lighting, artstation, concept art, 4K

NEGATIVE:
photorealistic, photograph, text, watermark, blurry, low quality, ugly, deformed
```

### Anime Character

```
POSITIVE:
masterpiece, best quality, anime style, 1girl, long silver hair, blue eyes, school uniform, cherry blossoms, wind blowing hair, detailed background, vibrant colors, cel shading

NEGATIVE:
photorealistic, 3D render, bad anatomy, extra fingers, missing fingers, blurry, low quality, watermark, text
```

### Cyberpunk Scene

```
POSITIVE:
masterpiece, best quality, cyberpunk cityscape, neon lights, rainy street, reflections, flying cars, holographic advertisements, night scene, cinematic lighting, blade runner style, highly detailed

NEGATIVE:
daytime, sunny, rural, nature, blurry, low quality, watermark, text, cartoon
```

### Oil Painting

```
POSITIVE:
masterpiece, best quality, oil painting, classical landscape, rolling hills, old oak tree, dramatic sky, golden hour, visible brushstrokes, canvas texture, Renaissance style, museum quality

NEGATIVE:
photograph, digital, 3D render, modern, blurry, low quality, watermark, text
```

---

## LoRA Syntax

When using LoRA models:

```
<lora:model_name:0.7>
```

**Rules:**
- Start at 0.7-0.8 strength
- Above 1.0 causes over-saturation
- Place at end of positive prompt

Example:
```
portrait of a warrior, armor, dramatic lighting <lora:CinematicPortraits:0.75>, masterpiece, best quality
```

---

## Common Mistakes to Avoid

| Mistake | Fix |
|---------|-----|
| Too many competing styles | Pick 1-2 complementary styles |
| Vague descriptions | Be specific: "red rose" not "flower" |
| Ignoring negative prompts | Always use appropriate negatives |
| Excessive weighting | Keep weights 0.7-1.3 range |
| No quality tags | Always start with quality boosters |
| Prompt too long | Focus on key elements, use BREAK |

---

## Workflow

1. **Gather requirements** - Subject, style, mood, composition
2. **Build positive prompt** - Quality tags + subject + style + lighting
3. **Build negative prompt** - Universal + style-specific negatives
4. **Suggest settings** - CFG, sampler, steps based on style
5. **Output formatted** - Ready to copy into SD interface

### Example Request

User: "Create a prompt for a mystical forest with fireflies at dusk"

### Example Output

```
POSITIVE PROMPT:
masterpiece, best quality, ultra detailed, mystical forest, ancient trees, glowing fireflies, magical atmosphere, dusk lighting, soft mist, volumetric light rays, enchanted, fantasy, digital painting, concept art, artstation, 4K, intricate detail

NEGATIVE PROMPT:
ugly, deformed, mutation, extra limbs, blurry, oversaturated, watermark, text, logo, poorly drawn, bad anatomy, artifacts, low quality, worst quality, jpeg artifacts, cartoon, anime, photograph, daytime, sunny

RECOMMENDED SETTINGS:
CFG Scale: 7
Sampler: DPM++ 2M Karras
Steps: 25
```
