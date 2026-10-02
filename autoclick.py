"""
AutoClick - grava movimentos e cliques do mouse e reproduz.

Atalhos globais (funcionam com a janela minimizada):
  F9  -> iniciar / parar gravação
  F10 -> iniciar reprodução
  Esc -> parar reprodução (ou gravação)
"""
import json
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from pynput import keyboard, mouse

BUTTONS = {"left": mouse.Button.left, "right": mouse.Button.right, "middle": mouse.Button.middle}


class AutoClick:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.events = []  # (t, tipo, dados...)
        self.recording = False
        self.playing = False
        self.stop_flag = threading.Event()
        self.t0 = 0.0
        self.mouse_ctl = mouse.Controller()
        self.mouse_listener = None

        root.title("AutoClick")
        root.resizable(False, False)
        root.attributes("-topmost", True)

        frm = ttk.Frame(root, padding=12)
        frm.grid()

        self.status = tk.StringVar(value="Pronto. F9 grava, F10 reproduz, Esc para.")
        ttk.Label(frm, textvariable=self.status, width=48).grid(row=0, column=0, columnspan=4, pady=(0, 8))

        self.btn_rec = ttk.Button(frm, text="● Gravar (F9)", command=lambda: self.toggle_record(True))
        self.btn_rec.grid(row=1, column=0, padx=2)
        ttk.Button(frm, text="▶ Reproduzir (F10)", command=self.start_play).grid(row=1, column=1, padx=2)
        ttk.Button(frm, text="■ Parar (Esc)", command=lambda: self.stop_all(True)).grid(row=1, column=2, padx=2)

        ttk.Label(frm, text="Repetições (0 = infinito):").grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.repeats = tk.IntVar(value=1)
        ttk.Spinbox(frm, from_=0, to=100000, textvariable=self.repeats, width=8).grid(row=2, column=2, sticky="w", pady=(10, 0))

        ttk.Label(frm, text="Velocidade (1 = normal):").grid(row=3, column=0, columnspan=2, sticky="w")
        self.speed = tk.DoubleVar(value=1.0)
        ttk.Spinbox(frm, from_=0.1, to=10, increment=0.1, textvariable=self.speed, width=8).grid(row=3, column=2, sticky="w")

        ttk.Label(frm, text="Pausa entre repetições (s):").grid(row=4, column=0, columnspan=2, sticky="w")
        self.gap = tk.DoubleVar(value=0.5)
        ttk.Spinbox(frm, from_=0, to=3600, increment=0.5, textvariable=self.gap, width=8).grid(row=4, column=2, sticky="w")

        ttk.Button(frm, text="Salvar...", command=self.save).grid(row=5, column=0, pady=(10, 0))
        ttk.Button(frm, text="Abrir...", command=self.load).grid(row=5, column=1, pady=(10, 0))

        self.kb_listener = keyboard.Listener(on_press=self.on_key)
        self.kb_listener.daemon = True
        self.kb_listener.start()

    # ---------- utilidades ----------
    def set_status(self, text):
        self.root.after(0, self.status.set, text)

    def on_key(self, key):
        if key == keyboard.Key.f9:
            self.root.after(0, self.toggle_record)
        elif key == keyboard.Key.f10:
            self.root.after(0, self.start_play)
        elif key == keyboard.Key.esc:
            self.root.after(0, self.stop_all)

    # ---------- gravação ----------
    def toggle_record(self, from_button=False):
        if self.playing:
            return
        if self.recording:
            self.stop_record(from_button)
        else:
            self.start_record()

    def start_record(self):
        self.events = []
        self.recording = True
        self.t0 = time.perf_counter()
        self.mouse_listener = mouse.Listener(
            on_move=self.on_move, on_click=self.on_click, on_scroll=self.on_scroll
        )
        self.mouse_listener.start()
        self.btn_rec.config(text="■ Parar gravação (F9)")
        self.set_status("Gravando... pressione F9 para parar.")

    def stop_record(self, from_button=False):
        self.recording = False
        if self.mouse_listener:
            self.mouse_listener.stop()
            self.mouse_listener = None
        # descarta o "soltar" do clique no botão Gravar
        while self.events and self.events[0][1] == "click" and not self.events[0][5]:
            self.events.pop(0)
        # se parou clicando no botão, descarta esse clique final
        if from_button:
            for i in range(len(self.events) - 1, -1, -1):
                if self.events[i][1] == "click" and self.events[i][5]:
                    del self.events[i:]
                    break
        self.btn_rec.config(text="● Gravar (F9)")
        clicks = sum(1 for e in self.events if e[1] == "click" and e[5])
        dur = self.events[-1][0] if self.events else 0
        self.set_status(f"Gravado: {len(self.events)} eventos, {clicks} cliques, {dur:.1f}s.")

    def _t(self):
        return time.perf_counter() - self.t0

    def on_move(self, x, y):
        if self.recording:
            self.events.append((self._t(), "move", x, y))

    def on_click(self, x, y, button, pressed):
        if self.recording and button.name in BUTTONS:
            self.events.append((self._t(), "click", x, y, button.name, pressed))

    def on_scroll(self, x, y, dx, dy):
        if self.recording:
            self.events.append((self._t(), "scroll", x, y, dx, dy))

    # ---------- reprodução ----------
    def start_play(self):
        if self.recording or self.playing:
            return
        if not self.events:
            self.set_status("Nada gravado ainda. Use F9 para gravar.")
            return
        try:
            repeats = int(self.repeats.get())
            speed = max(0.01, float(self.speed.get()))
            gap = max(0.0, float(self.gap.get()))
        except (tk.TclError, ValueError):
            messagebox.showerror("AutoClick", "Valores inválidos nas configurações.")
            return
        self.playing = True
        self.stop_flag.clear()
        threading.Thread(target=self._play, args=(repeats, speed, gap), daemon=True).start()

    def _sleep(self, secs):
        # dorme em pedaços para responder rápido ao Esc
        end = time.perf_counter() + secs
        while not self.stop_flag.is_set():
            left = end - time.perf_counter()
            if left <= 0:
                return True
            time.sleep(min(left, 0.01))
        return False

    def _play(self, repeats, speed, gap):
        n = 0
        pressed = set()
        try:
            while not self.stop_flag.is_set() and (repeats == 0 or n < repeats):
                n += 1
                total = "∞" if repeats == 0 else repeats
                self.set_status(f"Reproduzindo {n}/{total}... Esc para parar.")
                start = time.perf_counter()
                for ev in self.events:
                    wait = ev[0] / speed - (time.perf_counter() - start)
                    if wait > 0 and not self._sleep(wait):
                        break
                    if self.stop_flag.is_set():
                        break
                    kind = ev[1]
                    if kind == "move":
                        self.mouse_ctl.position = (ev[2], ev[3])
                    elif kind == "click":
                        self.mouse_ctl.position = (ev[2], ev[3])
                        btn = BUTTONS[ev[4]]
                        if ev[5]:
                            self.mouse_ctl.press(btn)
                            pressed.add(btn)
                        else:
                            self.mouse_ctl.release(btn)
                            pressed.discard(btn)
                    elif kind == "scroll":
                        self.mouse_ctl.position = (ev[2], ev[3])
                        self.mouse_ctl.scroll(ev[4], ev[5])
                if repeats == 0 or n < repeats:
                    self._sleep(gap)
        finally:
            for btn in pressed:  # nunca deixar botão "preso"
                self.mouse_ctl.release(btn)
            self.playing = False
            self.set_status("Interrompido." if self.stop_flag.is_set() else f"Concluído ({n} repetições).")

    def stop_all(self, from_button=False):
        if self.recording:
            self.stop_record(from_button)
        if self.playing:
            self.stop_flag.set()

    # ---------- arquivos ----------
    def save(self):
        if not self.events:
            self.set_status("Nada para salvar.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("Gravação", "*.json")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.events, f)
            self.set_status(f"Salvo em {path}")

    def load(self):
        path = filedialog.askopenfilename(filetypes=[("Gravação", "*.json")])
        if path:
            with open(path, encoding="utf-8") as f:
                self.events = [tuple(e) for e in json.load(f)]
            self.set_status(f"Carregado: {len(self.events)} eventos.")


if __name__ == "__main__":
    root = tk.Tk()
    AutoClick(root)
    root.mainloop()
