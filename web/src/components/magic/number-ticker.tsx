"use client";

// Magic UI's NumberTicker: counts up to the real value when scrolled into
// view. The target is always a live figure from the API -- nothing on this
// site animates toward a number the data cannot support.
import { useEffect, useRef, useState } from "react";
import { useInView } from "framer-motion";

export function NumberTicker({
  value,
  className,
  durationMs = 1200,
}: {
  value: number;
  className?: string;
  durationMs?: number;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, margin: "-32px" });
  const [display, setDisplay] = useState(0);

  useEffect(() => {
    if (!inView) return;
    if (value === 0) return setDisplay(0);

    let frame = 0;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min((now - start) / durationMs, 1);
      // easeOutExpo: fast start, long settle -- reads as "counting up" rather
      // than as a linear odometer.
      const eased = t === 1 ? 1 : 1 - Math.pow(2, -10 * t);
      setDisplay(Math.round(eased * value));
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [inView, value, durationMs]);

  return (
    <span ref={ref} className={className}>
      {display.toLocaleString()}
    </span>
  );
}
