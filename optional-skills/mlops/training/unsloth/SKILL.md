---
name: unsloth
description: "Unsloth: 2-5x faster LoRA/QLoRA fine-tuning, less VRAM."
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [unsloth, torch, transformers, trl, datasets, peft]
platforms: [linux, macos]
metadata:
  hermes:
    tags: [Fine-Tuning, Unsloth, Fast Training, LoRA, QLoRA, Memory-Efficient, Optimization, Llama, Mistral, Gemma, Qwen]

---

# Unsloth Skill

Assistance with unsloth development, generated from official documentation.

## When to Use This Skill

This skill should be triggered when:
- Working with unsloth
- Asking about unsloth features or APIs
- Implementing unsloth solutions
- Debugging unsloth code
- Learning unsloth best practices

## Quick Reference

### Common Patterns

**Pattern 1:** Load a pre-quantized model with room for long context via `max_seq_length`, for example:

```python
from unsloth import FastLanguageModel
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="unsloth/Qwen3-4B-Instruct-2507",
    max_seq_length=2048,
    load_in_4bit=True,
)
```

**Pattern 2:** Wrap the model with LoRA adapters, targeting all attention and MLP projections, for example:

```python
model = FastLanguageModel.get_peft_model(
    model,
    r=16,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                    "gate_proj", "up_proj", "down_proj"],
    lora_alpha=16,
    use_gradient_checkpointing="unsloth",
)
```

**Pattern 3:** Train with the TRL `SFTTrainer` on a supervised dataset, for example:

```python
from trl import SFTTrainer
from transformers import TrainingArguments
trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset,
    args=TrainingArguments(per_device_train_batch_size=2, num_train_epochs=1),
)
trainer.train()
```

**Pattern 4:** Export the fine-tune for local inference by saving a quantized GGUF, for example:

```python
model.save_pretrained_gguf("unsloth-finetune", tokenizer, quantization_method="q4_k_m")
```

**Pattern 5:** Run fast inference with the model's chat template applied, for example:

```python
from unsloth import FastLanguageModel
FastLanguageModel.for_inference(model)
inputs = tokenizer(["Explain LoRA in one sentence."], return_tensors="pt").to("cuda")
outputs = model.generate(**inputs, max_new_tokens=64)
print(tokenizer.batch_decode(outputs)[0])
```

## Reference Files

This skill includes full documentation in `references/`:

- **llms-txt.md** - Llms-Txt documentation
- **llms-full.md** - Full Unsloth documentation, including GRPO/reward-function, base-model and epoch guides absent from llms-txt.md
- **llms.md** - Upstream documentation link index

Use `view` to read specific reference files when detailed information is needed.

## Working with This Skill

### For Beginners
Start with the getting_started or tutorials reference files for foundational concepts.

### For Specific Features
Use the appropriate category reference file (api, guides, etc.) for detailed information.

### For Code Examples
The quick reference section above contains common patterns extracted from the official docs.

## Resources

### references/
Organized documentation extracted from official sources. These files contain:
- Detailed explanations
- Code examples with language annotations
- Links to original documentation
- Table of contents for quick navigation

### scripts/
Add helper scripts here for common automation tasks.

### assets/
Add templates, boilerplate, or example projects here.

## Notes

- This skill was automatically generated from official documentation
- Reference files preserve the structure and examples from source docs
- Code examples include language detection for better syntax highlighting
- Quick reference patterns are extracted from common usage examples in the docs

## Updating

To refresh this skill with updated documentation:
1. Re-run the scraper with the same configuration
2. The skill will be rebuilt with the latest information

<!-- Trigger re-upload 1763621536 -->



