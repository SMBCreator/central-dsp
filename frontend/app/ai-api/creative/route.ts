import { generateText, Output } from "ai"
import { z } from "zod"

export const maxDuration = 60

const schema = z.object({
  variants: z
    .array(
      z.object({
        title: z.string().describe("Заголовок объявления, до 60 символов, на русском"),
        description: z.string().describe("Описание объявления, до 120 символов, на русском"),
        cta: z.string().describe("Текст кнопки призыва к действию, 1-3 слова, на русском"),
      }),
    )
    .length(3),
})

export async function POST(req: Request) {
  try {
    const { product, audience, tone } = await req.json()
    if (!product || typeof product !== "string") {
      return Response.json({ detail: "Укажите продукт" }, { status: 400 })
    }

    const { output } = await generateText({
      model: "openai/gpt-5.4-mini",
      output: Output.object({ schema }),
      prompt: `Ты — опытный копирайтер performance-рекламы для рынка СНГ.
Придумай 3 варианта рекламного объявления на русском языке.

Продукт/услуга: ${product}
Целевая аудитория: ${audience || "широкая"}
Тональность: ${tone || "нейтральная"}

Требования: заголовок до 60 символов, описание до 120 символов, CTA 1-3 слова. Без кавычек и эмодзи. Конкретная выгода в каждом заголовке.`,
    })

    return Response.json(output)
  } catch (err) {
    console.error("[v0] AI creative error:", err)
    return Response.json({ detail: "Ошибка генерации. Попробуйте ещё раз." }, { status: 500 })
  }
}
