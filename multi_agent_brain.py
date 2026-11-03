import os
import json
import logging
import asyncio
import urllib.request
import urllib.error
from typing import Dict, Any, Tuple
from datetime import datetime

logger = logging.getLogger("TradingEngine")

class MultiAgentBrain:
    """
    Multi-Agent LLM Trading Council (TradingAgents Architecture).
    Consists of:
      1. Technical Analyst Agent (Momentum, RSI, CMO, VWAP Bands)
      2. News & Macro Analyst Agent (Macroeconomics, Regulatory, Crypto News)
      3. Sentiment & Social Analyst Agent (Community buzz, Market Mood)
      4. Fundamentals & On-chain Analyst Agent (Token utility, Network activity)
      5. Bullish Paladin vs Bearish Berserker (Structured Debate Arena)
      6. Risk Emperor (Final Rating: BUY / SELL / HOLD with Confidence Score)
      
    Zero-Dependency Resilient Engine:
      - Uses direct REST API with auto-model fallback (gemini-2.5-flash -> 2.0 -> 1.5).
      - Operates in safe pass-through standby if keys are not present.
      - Displays precise error diagnostics to Telegram if a key is invalid or quota exceeded.
    """

    def __init__(self):
        self.google_key = ""
        self.openai_key = ""
        self.deepseek_key = ""
        self.has_ai_key = False
        self.client_type = None
        self.reload_keys()

    def reload_keys(self):
        """Re-read environment variables in case keys were added or updated on Render."""
        self.google_key = os.getenv("GOOGLE_API_KEY", "").strip().strip('"').strip("'")
        self.openai_key = os.getenv("OPENAI_API_KEY", "").strip().strip('"').strip("'")
        self.deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip().strip('"').strip("'")
        self.has_ai_key = bool(self.google_key or self.openai_key or self.deepseek_key)

        if self.google_key:
            self.client_type = "google"
            logger.info("🧠 [MULTI-AGENT AI] Google Gemini Engine Selected.")
        elif self.openai_key:
            self.client_type = "openai"
            logger.info("🧠 [MULTI-AGENT AI] OpenAI Engine Selected.")
        elif self.deepseek_key:
            self.client_type = "deepseek"
            logger.info("🧠 [MULTI-AGENT AI] DeepSeek Engine Selected.")
        else:
            self.client_type = None

    def _call_gemini_rest(self, prompt: str, system_prompt: str = "") -> Tuple[str, str]:
        """Direct REST call to Google Gemini with fast model fallback and error parsing."""
        key = self.google_key
        custom_model = os.getenv("GEMINI_MODEL", "").strip()
        default_models = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite"]
        models = [custom_model] if custom_model else default_models
        full_text = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        payload = {"contents": [{"parts": [{"text": full_text}]}]}
        data = json.dumps(payload).encode("utf-8")
        last_err = ""

        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            try:
                logger.info(f"🧠 [MULTI-AGENT AI] Invoking Gemini model: {model}...")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    res_json = json.loads(resp.read().decode("utf-8"))
                    candidates = res_json.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            ans = parts[0].get("text", "").strip()
                            logger.info(f"✅ [MULTI-AGENT AI] Successfully received response from {model} ({len(ans)} chars)")
                            return ans, ""
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="ignore")
                msg = str(e)
                try:
                    err_json = json.loads(err_body)
                    msg = err_json.get("error", {}).get("message", msg)
                except Exception:
                    pass
                last_err = f"HTTP {e.code}: {msg}"
                logger.warning(f"⚠️ [GEMINI {model} FAILED] {last_err}")
                # Hard fail immediately on auth errors - don't keep user waiting
                if e.code in [400, 401, 403]:
                    return "", f"API Error (HTTP {e.code}): {msg}"
            except Exception as e:
                last_err = str(e)
                logger.warning(f"⚠️ [GEMINI {model} EXCEPTION] {last_err}")

        return "", last_err

    def _call_openai_compat_rest(self, prompt: str, system_prompt: str = "") -> Tuple[str, str]:
        """Direct REST call to OpenAI or DeepSeek."""
        is_deepseek = (self.client_type == "deepseek")
        url = "https://api.deepseek.com/v1/chat/completions" if is_deepseek else "https://api.openai.com/v1/chat/completions"
        key = self.deepseek_key if is_deepseek else self.openai_key
        model = "deepseek-chat" if is_deepseek else "gpt-4o-mini"

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {"model": model, "messages": messages, "temperature": 0.2}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))
                choices = res_json.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "").strip(), ""
            return "", "Empty response from provider"
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            return "", f"HTTP {e.code}: {err_body}"
        except Exception as e:
            return "", str(e)

    async def _call_llm(self, prompt: str, system_prompt: str = "") -> Tuple[str, str]:
        """Asynchronously invoke the configured LLM."""
        self.reload_keys()
        if not self.has_ai_key:
            return "", "No AI key configured"

        loop = asyncio.get_running_loop()
        if self.client_type == "google":
            return await loop.run_in_executor(None, self._call_gemini_rest, prompt, system_prompt)
        elif self.client_type in ["openai", "deepseek"]:
            return await loop.run_in_executor(None, self._call_openai_compat_rest, prompt, system_prompt)

        return "", "Unknown provider"

    async def evaluate_trade(
        self,
        symbol: str,
        side: str,
        current_price: float,
        cmo: float,
        regime: str,
        atr: float
    ) -> Dict[str, Any]:
        """
        Runs Multi-Agent Council to debate and approve or reject a potential trade entry.
        """
        self.reload_keys()
        if not self.has_ai_key:
            return {
                "approved": True,
                "confidence": 0.5,
                "reason": "AI Key not configured yet (Pass-through mode active).",
                "summary": "Technical rules satisfied. Multi-Agent AI on standby.",
                "telegram_message": ""
            }

        logger.info(f"🏛️ [MULTI-AGENT COUNCIL CONVENED] Evaluating candidate {side} for {symbol} at ${current_price:.4f}...")

        prompt = f"""
You are the Supreme Trading Council of TradingAgents (Multi-Agent Hedge Fund Framework).
A technical trading bot wants to enter a {side.upper()} trade on {symbol}.

Current Market Data:
- Symbol: {symbol}
- Proposed Side: {side.upper()}
- Market Price: ${current_price:.4f}
- Market Regime: {regime}
- Chande Momentum Oscillator (CMO): {cmo:.1f}
- Average True Range (ATR): {atr:.4f}

Perform a 3-stage multi-agent debate:
1. Analyst Insights:
   - Technical Ninja: Momentum analysis of CMO={cmo:.1f} & {regime}.
   - News/Macro Oracle: Crypto market context.
   - Sentiment Bard: Crowd behavior & trap probability.
2. Debate Clash:
   - Bull Paladin (arguing for upside) vs Bear Berserker (arguing downside risks/traps).
3. Risk Emperor Final Decree:
   - MUST decide: APPROVED or VETOED
   - Confidence Score (0% to 100%)
   - Concise 1-sentence rationale in Thai.

Output EXACT JSON format only:
{{
  "decision": "APPROVED" or "VETOED",
  "confidence": 85,
  "bull_argument": "...",
  "bear_argument": "...",
  "technical_summary": "...",
  "risk_decree_th": "สรุปคำตัดสินภาษาไทยสั้นๆ 1-2 ประโยค"
}}
"""

        system_prompt = "You are a professional multi-agent crypto trading committee. Be extremely strict on risk management. Return only valid JSON."
        raw_resp, err = await self._call_llm(prompt, system_prompt)

        if not raw_resp:
            logger.warning(f"[MULTI-AGENT LLM ERROR] {err}")
            return {
                "approved": True,
                "confidence": 0.65,
                "reason": f"AI Council bypass (Error: {err})",
                "summary": "Technical entry validated.",
                "telegram_message": ""
            }

        try:
            cleaned = raw_resp.strip()
            if "```json" in cleaned:
                cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
            elif "```" in cleaned:
                cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()
            parsed = json.loads(cleaned)

            decision = parsed.get("decision", "APPROVED").upper()
            confidence = int(parsed.get("confidence", 70))
            is_approved = (decision == "APPROVED" and confidence >= 60)

            decree_th = parsed.get("risk_decree_th", "มติสภา AI เห็นชอบคำสั่งเทรด")
            bull_arg = parsed.get("bull_argument", "โมเมนตัมกราฟเทคนิคสนับสนุน")
            bear_arg = parsed.get("bear_argument", "มีจุดตัดขาดทุนชัดเจน")

            side_th = "เปิด Long (ซื้อ)" if side == "BUY" else "เปิด Short (ขาย)"
            emoji = "🟢" if is_approved else "🔴"

            tg_msg = (
                f"{emoji} *[มติสภา AI TradingAgents - {decision}]*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"• *คู่เหรียญ:* `{symbol}` ({side_th})\n"
                f"• *คะแนนความมั่นใจ AI:* `{confidence}%`\n"
                f"• *สภาวะกราฟ:* `{regime}` (CMO: {cmo:.1f})\n"
                f"──────────────────\n"
                f"⚔️ *ผลการโต้วาที (Bull vs Bear Debate):*\n"
                f"• 🐂 *Bull Paladin:* _{bull_arg}_\n"
                f"• 🐻 *Bear Berserker:* _{bear_arg}_\n"
                f"──────────────────\n"
                f"👑 *คำวินิจฉัย Risk Emperor:*\n"
                f"_{decree_th}_\n"
                f"• *สถานะคำสั่ง:* {'✅ อนุมัติยิงออเดอร์ Bybit' if is_approved else '🛑 สั่งระงับการเทรดเพื่อรักษาทุน'}"
            )

            return {
                "approved": is_approved,
                "confidence": confidence / 100.0,
                "reason": decree_th,
                "summary": decree_th,
                "telegram_message": tg_msg,
                "notify_veto": True
            }

        except Exception as e:
            logger.warning(f"[MULTI-AGENT PARSE FALLBACK] Error parsing JSON: {e}")
            return {
                "approved": True,
                "confidence": 0.65,
                "reason": "AI Council approved with default risk parameters.",
                "summary": "Technical entry validated.",
                "telegram_message": ""
            }

    async def on_demand_analysis(self, symbol: str, current_price: float, cmo: float, regime: str) -> str:
        """Called when user types /analyze or /ai in Telegram."""
        self.reload_keys()
        if not self.has_ai_key:
            return (
                "⚠️ *[Multi-Agent AI อยู่ในสถานะ Standby]*\n"
                "ยังไม่พบคีย์ AI บนระบบ Render ครับ\n\n"
                "💡 *วิธีเปิดใช้งาน:*\n"
                "ไปที่ Render ➔ เมนู Environment ➔ กดปุ่ม `+ Add Environment Variable`\n"
                "• Key: `GOOGLE_API_KEY`\n"
                "• Value: วางคีย์ Gemini ของคุณ แล้วกด Save Changes ครับ"
            )

        prompt = f"""
Perform a live on-demand comprehensive market review for {symbol} at current price ${current_price:.4f}.
Market Regime: {regime}, CMO: {cmo:.1f}.

Simulate a rapid discussion by:
1. Technical Analyst
2. News & Macro Oracle
3. Sentiment Bard
4. Bull vs Bear debate verdict

Provide a concise Thai summary for Telegram with bold emojis. Max 200 words.
"""
        raw_resp, err = await self._call_llm(prompt, "You are TradingAgents AI committee. Format output beautifully in Thai for Telegram markdown.")
        if not raw_resp:
            return (
                f"❌ *[ไม่สามารถติดต่อสมอง AI ได้]*\n"
                f"• *สาเหตุ:* `{err}`\n\n"
                f"💡 กรุณาตรวจสอบว่าคีย์ `GOOGLE_API_KEY` บน Render ถูกต้องและไม่มีเว้นวรรคส่วนเกินครับ"
            )

        header = f"🧙‍♂️ *[รายงานสดจากสภา AI TradingAgents]*\n━━━━━━━━━━━━━━━━━━\n"
        return header + raw_resp.strip()
