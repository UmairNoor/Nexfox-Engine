//+------------------------------------------------------------------+
//| AurumScalp.mq5 — XAUUSD tick scalper controlled by the cloud     |
//|                                                                  |
//| Trades are decided HERE, on every tick, next to the broker.      |
//| The cloud (EA Gateway) supplies: license state, signed config,   |
//| commands (pause/resume/close_all/kill) and news blackout windows,|
//| and receives heartbeats + trade events for the dashboard.        |
//|                                                                  |
//| Setup: Tools > Options > Expert Advisors > Allow WebRequest for  |
//|        the API URL below. Attach to an XAUUSD M1 chart.          |
//|                                                                  |
//| STARTER CODE: compile in MetaEditor, then test in the Strategy   |
//| Tester and on DEMO for weeks before any live money.              |
//+------------------------------------------------------------------+
#property copyright "AurumScalp"
#property version   "2.41"
#property strict

#include <Trade/Trade.mqh>

input string InpLicenseKey  = "";                         // License key (AUSC-XXXX-XXXX-XXXX-XXXX)
input string InpApiUrl      = "https://api.aurumscalp.com"; // API base URL (must be allowed in WebRequest)
input string InpSignSecret  = "";                         // EA signing secret (baked into your release build)
input long   InpMagic       = 240901;                     // Magic number for this EA's orders
input int    InpTimerSec    = 2;                          // Internal timer (heartbeat every 5 ticks of this)

#define EA_VERSION "2.4.1"

CTrade trade;

//--- config received from the server (defaults are safe/conservative)
double   cfgRiskPct = 0.5, cfgDailyLossPct = 2.0, cfgDdStopPct = 8.0, cfgMaxLot = 0.5;
int      cfgMaxTradesDay = 20, cfgMaxOpen = 1, cfgMaxSpread = 22, cfgDeviation = 5;
int      cfgSlPts = 160, cfgTpPts = 320, cfgTrailPts = 90, cfgTimeStopSec = 180, cfgNewsMin = 30;
double   cfgBreakevenR = 1.0, cfgTrailStartR = 1.2;
bool     cfgNewsFilter = true, cfgTradingEnabled = false;
bool     sesAsia = false, sesLondon = true, sesOverlap = true, sesNewYork = true;
bool     modMomentum = true, modBreakout = true, trendOn = true;
int      momBurstPts = 120, momWindowSec = 4, momMinTicks = 9;
int      brkBars = 8, brkMaxRangePts = 250, brkBufferPts = 20;
int      trendFast = 20, trendSlow = 50;
int      cfgVersion = 0;

//--- state
bool     licenseValid = false;
datetime lastValidHandshake = 0;
bool     killed = false;
double   dayStartEquity = 0;
int      dayOfYear = -1;
int      tradesToday = 0;
long     newsTs[];
int      timerCount = 0;
string   pendingEvents[];
long     ackIds[];
int      maFast = INVALID_HANDLE, maSlow = INVALID_HANDLE;
//--- tick buffer for the momentum module
double   tbPrice[256];
ulong    tbTime[256];
int      tbHead = 0, tbCount = 0;
//--- position bookkeeping (ticket -> module, requested price)
ulong    posTicket[64];
string   posModule[64];
double   posReq[64];
int      posSpread[64];
uint     posLatency[64];
int      posN = 0;

//+------------------------------------------------------------------+
//| Small JSON helpers (server sends compact, sorted-key JSON)        |
//+------------------------------------------------------------------+
string JsonRaw(const string json, const string key, int from = 0)
  {
   string pat = "\"" + key + "\":";
   int p = StringFind(json, pat, from);
   if(p < 0) return "";
   p += StringLen(pat);
   int n = StringLen(json);
   ushort c = StringGetCharacter(json, p);
   if(c == '"')
     {
      int e = p + 1;
      while(e < n)
        {
         ushort ch = StringGetCharacter(json, e);
         if(ch == '\\') { e += 2; continue; }
         if(ch == '"') break;
         e++;
        }
      return StringSubstr(json, p + 1, e - p - 1);
     }
   if(c == '{' || c == '[')
     {
      ushort open = c, close = (c == '{') ? '}' : ']';
      int depth = 0, e = p;
      bool inStr = false;
      for(; e < n; e++)
        {
         ushort ch = StringGetCharacter(json, e);
         if(inStr) { if(ch == '\\') { e++; continue; } if(ch == '"') inStr = false; continue; }
         if(ch == '"') { inStr = true; continue; }
         if(ch == open) depth++;
         else if(ch == close) { depth--; if(depth == 0) break; }
        }
      return StringSubstr(json, p, e - p + 1);
     }
   int e = p;
   while(e < n)
     {
      ushort ch = StringGetCharacter(json, e);
      if(ch == ',' || ch == '}' || ch == ']') break;
      e++;
     }
   return StringSubstr(json, p, e - p);
  }
double JsonNum(const string json, const string key, double def) { string r = JsonRaw(json, key); return r == "" ? def : StringToDouble(r); }
bool   JsonBool(const string json, const string key, bool def) { string r = JsonRaw(json, key); return r == "" ? def : (r == "true"); }

string JsonUnescape(const string s)
  {
   string out = "";
   int n = StringLen(s);
   for(int i = 0; i < n; i++)
     {
      ushort c = StringGetCharacter(s, i);
      if(c == '\\' && i + 1 < n)
        {
         ushort d = StringGetCharacter(s, i + 1);
         if(d == '"' || d == '\\' || d == '/') { out += ShortToString(d); i++; continue; }
         if(d == 'n') { out += "\n"; i++; continue; }
        }
      out += ShortToString(c);
     }
   return out;
  }

//+------------------------------------------------------------------+
//| HMAC-SHA256 (verifies that responses really came from our server) |
//+------------------------------------------------------------------+
string Hex(const uchar &b[])
  {
   string h = "";
   for(int i = 0; i < ArraySize(b); i++) h += StringFormat("%02x", b[i]);
   return h;
  }
// UTF-8 bytes without the trailing zero that StringToCharArray adds.
void Utf8(const string s, uchar &out[])
  {
   int n = StringToCharArray(s, out, 0, WHOLE_ARRAY, CP_UTF8);
   if(n > 0 && out[n - 1] == 0) ArrayResize(out, n - 1);
  }

string HmacSha256(const string key, const string msg)
  {
   uchar k[], m[], empty[], kh[];
   Utf8(key, k);
   Utf8(msg, m);
   if(ArraySize(k) > 64) { CryptEncode(CRYPT_HASH_SHA256, k, empty, kh); ArrayResize(k, 0); ArrayCopy(k, kh); }
   int klen = ArraySize(k);
   ArrayResize(k, 64);
   for(int i = klen; i < 64; i++) k[i] = 0;
   uchar ipad[64], opad[64];
   for(int i = 0; i < 64; i++) { ipad[i] = (uchar)(k[i] ^ 0x36); opad[i] = (uchar)(k[i] ^ 0x5c); }
   uchar inner[], innerHash[], outer[], outerHash[];
   ArrayResize(inner, 64 + ArraySize(m));
   ArrayCopy(inner, ipad, 0, 0, 64);
   ArrayCopy(inner, m, 64, 0, ArraySize(m));
   CryptEncode(CRYPT_HASH_SHA256, inner, empty, innerHash);
   ArrayResize(outer, 64 + ArraySize(innerHash));
   ArrayCopy(outer, opad, 0, 0, 64);
   ArrayCopy(outer, innerHash, 64, 0, ArraySize(innerHash));
   CryptEncode(CRYPT_HASH_SHA256, outer, empty, outerHash);
   return Hex(outerHash);
  }

//+------------------------------------------------------------------+
//| HTTP                                                               |
//+------------------------------------------------------------------+
string AuthJson()
  {
   string type = (AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL) ? "live" : "demo";
   return StringFormat("\"license_key\":\"%s\",\"login\":\"%I64d\",\"server\":\"%s\",\"ea_version\":\"%s\",\"account_type\":\"%s\"",
                       InpLicenseKey, AccountInfoInteger(ACCOUNT_LOGIN), AccountInfoString(ACCOUNT_SERVER), EA_VERSION, type);
  }

// POSTs {auth..., extra...}; returns the verified inner payload, or "" on any failure.
string Post(const string path, const string extra)
  {
   string body = "{" + AuthJson() + (extra == "" ? "" : "," + extra) + "}";
   uchar data[], result[];
   Utf8(body, data);
   string headers = "Content-Type: application/json\r\n", resHeaders;
   ResetLastError();
   int code = WebRequest("POST", InpApiUrl + path, headers, 5000, data, result, resHeaders);
   if(code == -1) { PrintFormat("WebRequest failed (%d). Add %s in Tools > Options > Expert Advisors.", GetLastError(), InpApiUrl); return ""; }
   string resp = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   if(code != 200) { PrintFormat("%s -> HTTP %d %s", path, code, resp); if(code == 401 || code == 403) licenseValid = false; return ""; }
   string payload = JsonUnescape(JsonRaw(resp, "payload"));
   string sig = JsonRaw(resp, "sig");
   if(InpSignSecret == "" || HmacSha256(InpSignSecret, payload) != sig) { Print("Signature check failed: refusing to trade."); licenseValid = false; return ""; }
   if((long)JsonNum(payload, "exp", 0) < (long)TimeGMT() - 60) { Print("Server response expired."); return ""; }
   return payload;
  }

//+------------------------------------------------------------------+
//| Config                                                              |
//+------------------------------------------------------------------+
void ApplyConfig(const string cfg)
  {
   cfgRiskPct       = JsonNum(cfg, "risk_pct", cfgRiskPct);
   cfgDailyLossPct  = JsonNum(cfg, "daily_loss_pct", cfgDailyLossPct);
   cfgDdStopPct     = JsonNum(cfg, "equity_dd_stop_pct", cfgDdStopPct);
   cfgMaxLot        = JsonNum(cfg, "max_lot", cfgMaxLot);
   cfgMaxTradesDay  = (int)JsonNum(cfg, "max_trades_day", cfgMaxTradesDay);
   cfgMaxOpen       = (int)JsonNum(cfg, "max_open", cfgMaxOpen);
   cfgMaxSpread     = (int)JsonNum(cfg, "max_spread_pts", cfgMaxSpread);
   cfgDeviation     = (int)JsonNum(cfg, "deviation_pts", cfgDeviation);
   cfgSlPts         = (int)JsonNum(cfg, "sl_pts", cfgSlPts);
   cfgTpPts         = (int)JsonNum(cfg, "tp_pts", cfgTpPts);
   cfgTrailPts      = (int)JsonNum(cfg, "trail_pts", cfgTrailPts);
   cfgTimeStopSec   = (int)JsonNum(cfg, "time_stop_sec", cfgTimeStopSec);
   cfgBreakevenR    = JsonNum(cfg, "breakeven_at_r", cfgBreakevenR);
   cfgTrailStartR   = JsonNum(cfg, "trail_start_r", cfgTrailStartR);
   cfgNewsFilter    = JsonBool(cfg, "news_filter", cfgNewsFilter);
   cfgNewsMin       = (int)JsonNum(cfg, "news_blackout_min", cfgNewsMin);
   cfgTradingEnabled= JsonBool(cfg, "trading_enabled", cfgTradingEnabled);
   cfgVersion       = (int)JsonNum(cfg, "config_version", cfgVersion);
   string ses = JsonRaw(cfg, "sessions");
   sesAsia = StringFind(ses, "asia") >= 0; sesLondon = StringFind(ses, "london") >= 0;
   sesOverlap = StringFind(ses, "overlap") >= 0; sesNewYork = StringFind(ses, "newyork") >= 0;
   string mods = JsonRaw(cfg, "modules");
   string mom = JsonRaw(mods, "momentum"), brk = JsonRaw(mods, "breakout");
   modMomentum  = JsonBool(mom, "enabled", modMomentum);
   momBurstPts  = (int)JsonNum(mom, "burst_pts", momBurstPts);
   momWindowSec = (int)JsonNum(mom, "window_sec", momWindowSec);
   momMinTicks  = (int)JsonNum(mom, "min_ticks", momMinTicks);
   modBreakout  = JsonBool(brk, "enabled", modBreakout);
   brkBars      = (int)JsonNum(brk, "range_bars", brkBars);
   brkMaxRangePts = (int)JsonNum(brk, "max_range_pts", brkMaxRangePts);
   brkBufferPts = (int)JsonNum(brk, "buffer_pts", brkBufferPts);
   string tr = JsonRaw(cfg, "trend_filter");
   trendOn = JsonBool(tr, "enabled", trendOn);
   int f = (int)JsonNum(tr, "ema_fast", trendFast), s = (int)JsonNum(tr, "ema_slow", trendSlow);
   if(f != trendFast || s != trendSlow || maFast == INVALID_HANDLE)
     {
      trendFast = f; trendSlow = s;
      if(maFast != INVALID_HANDLE) IndicatorRelease(maFast);
      if(maSlow != INVALID_HANDLE) IndicatorRelease(maSlow);
      maFast = iMA(_Symbol, PERIOD_M15, trendFast, 0, MODE_EMA, PRICE_CLOSE);
      maSlow = iMA(_Symbol, PERIOD_M15, trendSlow, 0, MODE_EMA, PRICE_CLOSE);
     }
   trade.SetDeviationInPoints(cfgDeviation);
   PrintFormat("Config v%d applied: preset=%s risk=%.2f%% maxSpread=%d", cfgVersion, JsonRaw(cfg, "preset"), cfgRiskPct, cfgMaxSpread);
  }

void ParseNews(const string payload)
  {
   ArrayResize(newsTs, 0);
   string arr = JsonRaw(payload, "news");
   int from = 0;
   while(true)
     {
      int p = StringFind(arr, "\"ts\":", from);
      if(p < 0) break;
      string v = JsonRaw(StringSubstr(arr, p - 1), "ts");
      int n = ArraySize(newsTs); ArrayResize(newsTs, n + 1); newsTs[n] = (long)StringToInteger(v);
      from = p + 5;
     }
  }

void RunCommands(const string payload)
  {
   string arr = JsonRaw(payload, "commands");
   int from = 0;
   while(true)
     {
      int p = StringFind(arr, "{", from);
      if(p < 0) break;
      int e = StringFind(arr, "}", p);
      string obj = StringSubstr(arr, p, e - p + 1);
      long id = (long)JsonNum(obj, "id", 0);
      string type = JsonRaw(obj, "type");
      PrintFormat("Command %I64d: %s", id, type);
      if(type == "close_all" || type == "kill") CloseAll(type == "kill" ? "Kill switch" : "Manual close");
      if(type == "pause" || type == "kill") cfgTradingEnabled = false;
      if(type == "kill") killed = true;
      if(type == "resume") { cfgTradingEnabled = true; killed = false; }
      int n = ArraySize(ackIds); ArrayResize(ackIds, n + 1); ackIds[n] = id;
      from = e + 1;
     }
  }

bool Handshake()
  {
   string p = Post("/ea/v1/handshake", "");
   if(p == "") return false;
   string lic = JsonRaw(p, "license");
   licenseValid = JsonBool(lic, "valid", false);
   if(!licenseValid) PrintFormat("License not valid (%s): no new trades.", JsonRaw(lic, "reason"));
   lastValidHandshake = TimeGMT();
   ApplyConfig(JsonRaw(p, "config"));
   ParseNews(p);
   RunCommands(p);
   return true;
  }

void UploadHistory()
  {
   MqlRates r[];
   int n = CopyRates(_Symbol, PERIOD_M1, 1, 240, r);
   if(n <= 0) return;
   string bars = "";
   for(int i = 0; i < n; i++)
      bars += StringFormat("%s{\"time\":%I64d,\"open\":%.3f,\"high\":%.3f,\"low\":%.3f,\"close\":%.3f}", i ? "," : "",
                           (long)r[i].time - (long)(TimeCurrent() - TimeGMT()), r[i].open, r[i].high, r[i].low, r[i].close);
   Post("/ea/v1/candles", "\"timeframe\":\"M1\",\"bars\":[" + bars + "]");
  }

void Heartbeat()
  {
   MqlTick t; SymbolInfoTick(_Symbol, t);
   string acks = "";
   for(int i = 0; i < ArraySize(ackIds); i++) acks += (i ? "," : "") + IntegerToString(ackIds[i]);
   string state = killed ? "stopped" : (!cfgTradingEnabled ? "paused" : (CanOpen(false) ? "running" : "blocked"));
   string extra = StringFormat("\"balance\":%.2f,\"equity\":%.2f,\"bid\":%.3f,\"ask\":%.3f,\"spread\":%d,\"ping_ms\":%d,\"open_positions\":%d,\"state\":\"%s\",\"applied_config_version\":%d,\"acked_commands\":[%s]",
                               AccountInfoDouble(ACCOUNT_BALANCE), AccountInfoDouble(ACCOUNT_EQUITY), t.bid, t.ask,
                               (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD), (int)(TerminalInfoInteger(TERMINAL_PING_LAST) / 1000),
                               CountOpen(), state, cfgVersion, acks);
   string p = Post("/ea/v1/heartbeat", extra);
   if(p == "") return;
   ArrayResize(ackIds, 0);
   licenseValid = JsonBool(JsonRaw(p, "license"), "valid", false);
   if(!JsonBool(p, "trading_enabled", true)) cfgTradingEnabled = false;
   RunCommands(p);
   if((int)JsonNum(p, "config_version", cfgVersion) > cfgVersion) Handshake();
  }

void FlushEvents()
  {
   int n = ArraySize(pendingEvents);
   if(n == 0) return;
   string ev = "";
   for(int i = 0; i < n; i++) ev += (i ? "," : "") + pendingEvents[i];
   if(Post("/ea/v1/trades", "\"events\":[" + ev + "]") != "") ArrayResize(pendingEvents, 0);
  }
void QueueEvent(const string json) { int n = ArraySize(pendingEvents); ArrayResize(pendingEvents, n + 1); pendingEvents[n] = json; }

//+------------------------------------------------------------------+
//| Risk & filters                                                     |
//+------------------------------------------------------------------+
int CountOpen()
  {
   int c = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(PositionGetTicket(i) > 0 && PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == InpMagic) c++;
   return c;
  }

bool InSession()
  {
   MqlDateTime g; TimeToStruct(TimeGMT(), g);
   int h = g.hour;
   if(h >= 21 && h < 23) return false;                // rollover: wide spreads
   if(h >= 7 && h < 12)  return sesLondon;
   if(h >= 12 && h < 16) return sesOverlap;
   if(h >= 16 && h < 21) return sesNewYork;
   return sesAsia;
  }

bool InNews()
  {
   if(!cfgNewsFilter) return false;
   long now = (long)TimeGMT();
   for(int i = 0; i < ArraySize(newsTs); i++) if(MathAbs(now - newsTs[i]) <= cfgNewsMin * 60) return true;
   return false;
  }

void RollDay()
  {
   MqlDateTime d; TimeToStruct(TimeCurrent(), d);
   if(d.day_of_year != dayOfYear) { dayOfYear = d.day_of_year; dayStartEquity = AccountInfoDouble(ACCOUNT_EQUITY); tradesToday = 0; }
  }

bool CanOpen(bool log)
  {
   string why = "";
   if(!licenseValid) why = "license";
   else if(TimeGMT() - lastValidHandshake > 24 * 3600) why = "no license check in 24 h";
   else if(killed || !cfgTradingEnabled) why = "paused";
   else if((int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD) > cfgMaxSpread) why = "spread";
   else if(!InSession()) why = "session";
   else if(InNews()) why = "news";
   else if(tradesToday >= cfgMaxTradesDay) why = "max trades";
   else if(CountOpen() >= cfgMaxOpen) why = "max open";
   else if(dayStartEquity > 0 && (dayStartEquity - AccountInfoDouble(ACCOUNT_EQUITY)) / dayStartEquity * 100.0 >= cfgDailyLossPct) why = "daily loss";
   if(log && why != "") Comment("AurumScalp: waiting (" + why + ")");
   return why == "";
  }

double CalcLots(double slDistance)
  {
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE), tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP), vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN), vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if(tickValue <= 0 || tickSize <= 0 || slDistance <= 0) return 0;
   double riskMoney = AccountInfoDouble(ACCOUNT_EQUITY) * cfgRiskPct / 100.0;
   double lots = riskMoney / (slDistance / tickSize * tickValue);
   lots = MathFloor(lots / step) * step;          // round DOWN to the lot step
   lots = MathMin(lots, MathMin(vmax, cfgMaxLot));
   return lots < vmin ? 0 : NormalizeDouble(lots, 2);
  }

int TrendDir()
  {
   if(!trendOn || maFast == INVALID_HANDLE) return 0;
   double f[1], s[1];
   if(CopyBuffer(maFast, 0, 0, 1, f) < 1 || CopyBuffer(maSlow, 0, 0, 1, s) < 1) return 0;
   return f[0] > s[0] ? 1 : (f[0] < s[0] ? -1 : 0);
  }

//+------------------------------------------------------------------+
//| Entry modules                                                      |
//+------------------------------------------------------------------+
int MomentumSignal(const MqlTick &t)
  {
   if(!modMomentum) return 0;
   double mid = (t.bid + t.ask) / 2.0;
   tbPrice[tbHead] = mid; tbTime[tbHead] = t.time_msc;
   tbHead = (tbHead + 1) % 256; if(tbCount < 256) tbCount++;
   ulong cutoff = t.time_msc - (ulong)momWindowSec * 1000;
   int n = 0; double oldest = mid;
   for(int i = 1; i <= tbCount; i++)
     {
      int idx = (tbHead - i + 256) % 256;
      if(tbTime[idx] < cutoff) break;
      oldest = tbPrice[idx]; n++;
     }
   if(n < momMinTicks) return 0;
   double move = (mid - oldest) / _Point;
   if(move >= momBurstPts) return 1;
   if(move <= -momBurstPts) return -1;
   return 0;
  }

int BreakoutSignal(const MqlTick &t)
  {
   if(!modBreakout) return 0;
   double hi[], lo[];
   if(CopyHigh(_Symbol, PERIOD_M1, 1, brkBars, hi) < brkBars || CopyLow(_Symbol, PERIOD_M1, 1, brkBars, lo) < brkBars) return 0;
   double h = hi[ArrayMaximum(hi)], l = lo[ArrayMinimum(lo)];
   if((h - l) / _Point > brkMaxRangePts) return 0;
   if(t.bid > h + brkBufferPts * _Point) return 1;
   if(t.ask < l - brkBufferPts * _Point) return -1;
   return 0;
  }

void Remember(ulong ticket, string module, double req, int spread, uint latency)
  {
   int i = posN < 64 ? posN++ : 0;
   posTicket[i] = ticket; posModule[i] = module; posReq[i] = req; posSpread[i] = spread; posLatency[i] = latency;
  }
int Find(ulong ticket) { for(int i = 0; i < posN; i++) if(posTicket[i] == ticket) return i; return -1; }

void Open(int dir, string module)
  {
   MqlTick t; SymbolInfoTick(_Symbol, t);
   double sl = cfgSlPts * _Point, tp = cfgTpPts * _Point;
   double lots = CalcLots(sl);
   if(lots <= 0) { Comment("AurumScalp: lot size below broker minimum"); return; }
   double req = dir > 0 ? t.ask : t.bid;
   double slP = dir > 0 ? req - sl : req + sl, tpP = dir > 0 ? req + tp : req - tp;
   int spread = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   uint t0 = GetTickCount();
   bool ok = dir > 0 ? trade.Buy(lots, _Symbol, 0, slP, tpP, module) : trade.Sell(lots, _Symbol, 0, slP, tpP, module);
   if(!ok) { PrintFormat("Order failed: %d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription()); return; }
   tradesToday++;
   ulong deal = trade.ResultDeal();
   if(HistoryDealSelect(deal)) Remember((ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID), module, req, spread, GetTickCount() - t0);
  }

//+------------------------------------------------------------------+
//| Trade management: break-even, trailing, time stop                  |
//+------------------------------------------------------------------+
void Manage()
  {
   MqlTick t; SymbolInfoTick(_Symbol, t);
   double r = cfgSlPts * _Point;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(tk == 0 || PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      bool buy = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY;
      double open = PositionGetDouble(POSITION_PRICE_OPEN), sl = PositionGetDouble(POSITION_SL), tp = PositionGetDouble(POSITION_TP);
      double px = buy ? t.bid : t.ask, gain = buy ? px - open : open - px;
      if((long)TimeCurrent() - (long)PositionGetInteger(POSITION_TIME) > cfgTimeStopSec && gain < r * 0.5) { trade.PositionClose(tk); continue; }
      double newSl = sl;
      if(cfgBreakevenR > 0 && gain >= r * cfgBreakevenR) newSl = buy ? MathMax(sl, open + 2 * _Point) : (sl == 0 ? open - 2 * _Point : MathMin(sl, open - 2 * _Point));
      if(cfgTrailStartR > 0 && gain >= r * cfgTrailStartR)
        {
         double trail = buy ? px - cfgTrailPts * _Point : px + cfgTrailPts * _Point;
         newSl = buy ? MathMax(newSl, trail) : MathMin(newSl, trail);
        }
      if(MathAbs(newSl - sl) >= _Point) trade.PositionModify(tk, NormalizeDouble(newSl, _Digits), tp);
     }
  }

void CloseAll(string reason)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong tk = PositionGetTicket(i);
      if(tk > 0 && PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == InpMagic) trade.PositionClose(tk);
     }
   PrintFormat("Closed all positions: %s", reason);
  }

//+------------------------------------------------------------------+
//| Events                                                             |
//+------------------------------------------------------------------+
int OnInit()
  {
   if(InpLicenseKey == "") { Alert("AurumScalp: paste your license key in the Inputs tab."); return INIT_PARAMETERS_INCORRECT; }
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetTypeFillingBySymbol(_Symbol);
   RollDay();
   if(Handshake()) UploadHistory();
   EventSetTimer(InpTimerSec);
   Comment("AurumScalp ", EA_VERSION, licenseValid ? " · connected" : " · waiting for license");
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason) { EventKillTimer(); FlushEvents(); Comment(""); }

void OnTimer()
  {
   timerCount++;
   FlushEvents();
   if(timerCount % 5 == 0) Heartbeat();           // every ~10 s with the default 2 s timer
   if(dayStartEquity > 0 && (dayStartEquity - AccountInfoDouble(ACCOUNT_EQUITY)) / dayStartEquity * 100.0 >= cfgDdStopPct && !killed)
     { CloseAll("Equity drawdown stop"); cfgTradingEnabled = false; }
  }

void OnTick()
  {
   RollDay();
   Manage();
   MqlTick t; if(!SymbolInfoTick(_Symbol, t)) return;
   int mom = MomentumSignal(t);                  // always feed the tick buffer
   if(!CanOpen(true)) return;
   Comment("AurumScalp: scanning");
   int trend = TrendDir();
   int brk = BreakoutSignal(t);
   if(mom != 0 && (trend == 0 || trend == mom)) { Open(mom, "momentum"); return; }
   if(brk != 0 && (trend == 0 || trend == brk)) Open(brk, "breakout");
  }

void OnTradeTransaction(const MqlTradeTransaction &tx, const MqlTradeRequest &req, const MqlTradeResult &res)
  {
   if(tx.type != TRADE_TRANSACTION_DEAL_ADD || !HistoryDealSelect(tx.deal)) return;
   if(HistoryDealGetInteger(tx.deal, DEAL_MAGIC) != InpMagic || HistoryDealGetString(tx.deal, DEAL_SYMBOL) != _Symbol) return;
   long entry = HistoryDealGetInteger(tx.deal, DEAL_ENTRY);
   ulong pos = (ulong)HistoryDealGetInteger(tx.deal, DEAL_POSITION_ID);
   long type = HistoryDealGetInteger(tx.deal, DEAL_TYPE);
   double price = HistoryDealGetDouble(tx.deal, DEAL_PRICE), vol = HistoryDealGetDouble(tx.deal, DEAL_VOLUME);
   long tm = (long)HistoryDealGetInteger(tx.deal, DEAL_TIME) - (long)(TimeCurrent() - TimeGMT());
   int k = Find(pos);
   if(entry == DEAL_ENTRY_IN)
     {
      string side = type == DEAL_TYPE_BUY ? "BUY" : "SELL";
      double slip = k >= 0 ? MathAbs(price - posReq[k]) / _Point : 0;
      QueueEvent(StringFormat("{\"type\":\"open\",\"ticket\":\"%I64u\",\"side\":\"%s\",\"volume\":%.2f,\"price\":%.3f,\"time\":%I64d,\"requested_price\":%.3f,\"sl\":%.3f,\"tp\":%.3f,\"slippage_pts\":%.1f,\"spread\":%d,\"latency_ms\":%d,\"module\":\"%s\"}",
                              pos, side, vol, price, tm, k >= 0 ? posReq[k] : price, PositionSelectByTicket(pos) ? PositionGetDouble(POSITION_SL) : 0.0,
                              PositionSelectByTicket(pos) ? PositionGetDouble(POSITION_TP) : 0.0, slip, k >= 0 ? posSpread[k] : 0, k >= 0 ? (int)posLatency[k] : 0, k >= 0 ? posModule[k] : "manual"));
     }
   else if(entry == DEAL_ENTRY_OUT)
     {
      string side = type == DEAL_TYPE_SELL ? "BUY" : "SELL";   // closing deal is opposite to the position
      long rs = HistoryDealGetInteger(tx.deal, DEAL_REASON);
      string reason = rs == DEAL_REASON_SL ? "SL" : rs == DEAL_REASON_TP ? "TP" : rs == DEAL_REASON_SO ? "Stop out" : killed ? "Kill switch" : "EA exit";
      QueueEvent(StringFormat("{\"type\":\"close\",\"ticket\":\"%I64u\",\"side\":\"%s\",\"volume\":%.2f,\"price\":%.3f,\"time\":%I64d,\"profit\":%.2f,\"commission\":%.2f,\"swap\":%.2f,\"reason\":\"%s\"}",
                              pos, side, vol, price, tm, HistoryDealGetDouble(tx.deal, DEAL_PROFIT), HistoryDealGetDouble(tx.deal, DEAL_COMMISSION),
                              HistoryDealGetDouble(tx.deal, DEAL_SWAP), reason));
     }
  }
//+------------------------------------------------------------------+
