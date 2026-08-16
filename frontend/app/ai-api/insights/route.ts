import { generateText, Output } from "ai"
import { z } from "zod"

export const maxDuration = 60

const schema = z.object({
  insights: z
    .array(
      z.object({
        severity: z.enum(["info", "warning", "critical"]).describe("Важность наблюдения"),
        title: z.string().describe("Краткий вывод, на русском"),
        recommendation: z.string().describe("Конкретная рекомендация по оптимизации, на русском"),
      }),
    )
    .min(2)
    .max(5),
})

export async function POST(req: Request) {
  try {
    const { stats } = await req.json()
    if (!stats) {
      return Response.json({ detail: "Нет данных для анализа" }, { status: 400 })
    }

    const { output } = await generateText({
      model: "openai/gpt-5.4-mini",
      output: Output.object({ schema }),
      prompt: `Ты — эксперт по programmatic-закупке рекламы (DSP, OpenRTB).
Проанализируй показатели рекламного аккаунта и дай выводы с рекомендациями по оптимизации на русском языке.

Данные (JSON):
${JSON.stringify(stats, null, 2)}

Ориентиры рынка: нормальный win rate 5-30%, CTR баннеров 0.05-0.3%, CTR натива 0.3-1%. Если данных мало, отметь это и предложи, что запустить. Рекомендации должны быть конкретными действиями (изменить ставку, таргетинг, паузу и т.п.).`,
    })

    return Response.json(output)
  } catch (err) {
    console.error("[v0] AI insights error:", err)
    return Response.json({ detail: "Ошибка анализа. Попробуйте ещё раз." }, { status: 500 })
  }
}
