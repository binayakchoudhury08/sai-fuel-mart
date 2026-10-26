"""Stand-in for Google Gemini: returns the transcription of the sample SVT bill page (No. 1070)."""
import json, os
from http.server import BaseHTTPRequestHandler, HTTPServer
PAGE = {"bill_no": "1070", "bill_date": "26.10.26", "party": "S.V.P", "rows": [
  {"sl": "1", "date": "25.10.26", "credit_bill_no": "", "particulars": "OD04R5758", "quantity": "110", "rate": "101.98", "amount": "11218"},
  {"sl": "2", "date": "''", "credit_bill_no": "", "particulars": "CASH ''", "quantity": "", "rate": "", "amount": "1000"},
  {"sl": "3", "date": "''", "credit_bill_no": "", "particulars": "OD19AA5279", "quantity": "218", "rate": "''", "amount": "22232"},
  {"sl": "4", "date": "''", "credit_bill_no": "", "particulars": "CASH ''", "quantity": "", "rate": "", "amount": "3000"},
  {"sl": "5", "date": "''", "credit_bill_no": "", "particulars": "ODO2CL2129", "quantity": "210", "rate": "''", "amount": "21416"},
  {"sl": "6", "date": "''", "credit_bill_no": "", "particulars": "CASH ''", "quantity": "", "rate": "", "amount": "3000"},
  {"sl": "7", "date": "''", "credit_bill_no": "", "particulars": "OD02CK9062", "quantity": "110", "rate": "''", "amount": "11218"},
  {"sl": "8", "date": "''", "credit_bill_no": "", "particulars": "CASH ''", "quantity": "", "rate": "", "amount": "1000"},
  {"sl": "9", "date": "''", "credit_bill_no": "", "particulars": "OD02CB9566", "quantity": "110", "rate": "''", "amount": "11218"},
  {"sl": "10", "date": "''", "credit_bill_no": "", "particulars": "CASH ''", "quantity": "", "rate": "", "amount": "1000"},
  {"sl": "11", "date": "''", "credit_bill_no": "", "particulars": "OD02CK7366", "quantity": "210", "rate": "''", "amount": "21416"},
  {"sl": "12", "date": "''", "credit_bill_no": "", "particulars": "CASH ''", "quantity": "", "rate": "", "amount": "3000"}],
  "total_quantity": "968", "total_amount": "110718", "amount_in_words": "One lakh ten thousand seven hundred eighteen only"}
PAGE_1071 = {"bill_no": "1071", "bill_date": "26.10.26", "party": "S.V.P", "rows": [
  {"sl": "1", "date": "26.10.26", "credit_bill_no": "88", "particulars": "OD02CL2129", "quantity": "200", "rate": "101.98", "amount": "20396"},
  {"sl": "2", "date": "''", "credit_bill_no": "''", "particulars": "CASH ''", "quantity": "", "rate": "", "amount": "2000"},
  {"sl": "3", "date": "''", "credit_bill_no": "89", "particulars": "OD02CB9566", "quantity": "", "rate": "", "amount": "1500"},
  {"sl": "4", "date": "''", "credit_bill_no": "90", "particulars": "OD15T4411", "quantity": "150", "rate": "''", "amount": "15297"},
  {"sl": "5", "date": "''", "credit_bill_no": "''", "particulars": "CASH ''", "quantity": "", "rate": "", "amount": "1000"}],
  "total_quantity": "350", "total_amount": "40193"}
PAGE_1072 = {"bill_no": "1072", "bill_date": "26.10.26", "party": "SVP", "rows": [
  {"sl": "1", "date": "26.10.26", "credit_bill_no": "", "particulars": "OD19AA5279", "quantity": "100", "rate": "101.98", "amount": "10500"},
  {"sl": "2", "date": "''", "credit_bill_no": "", "particulars": "CASH ''", "quantity": "", "rate": "", "amount": "500"}],
  "total_quantity": "100", "total_amount": "11000"}
PAGES = [PAGE, PAGE_1071, PAGE_1072]
_n = {"i": 0}


def next_page():
    pg = PAGES[_n["i"] % len(PAGES)]; _n["i"] += 1
    return pg


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        ok = self.headers.get("x-goog-api-key") == "testkey"
        parts = body["contents"][0]["parts"]
        if ok and "Reply with the single word OK" in parts[-1].get("text", ""):
            data = json.dumps({"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data)))
            self.end_headers(); self.wfile.write(data); return
        out = {"candidates": [{"content": {"parts": [{"text": "```json\n" + json.dumps(next_page()) + "\n```"}]}}]} if ok else {"error": "bad"}
        data = json.dumps(out).encode(); self.send_response(200 if ok else 403); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        data = json.dumps({"models": [{"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]},
                                      {"name": "models/gemini-3.5-flash-lite", "supportedGenerationMethods": ["generateContent"]}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(os.environ.get("PORT", "5199"))), H).serve_forever()
