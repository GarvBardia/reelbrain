"use client";

// Magic UI's BlurFade pattern: content fades up out of a slight blur when it
// scrolls into view. Like shadcn, Magic UI is copy-into-your-project source
// rather than an npm package, so this file is the component.
//
// `once: true` matters -- re-animating on every scroll-back is the thing that
// makes scroll-triggered sites feel cheap.
import { motion, useInView } from "framer-motion";
import { useRef, type ReactNode } from "react";

export function BlurFade({
  children,
  className,
  delay = 0,
  yOffset = 16,
}: {
  children: ReactNode;
  className?: string;
  delay?: number;
  yOffset?: number;
}) {
  const ref = useRef(null);
  const inView = useInView(ref, { once: true, margin: "-64px" });

  return (
    <motion.div
      ref={ref}
      initial={{ opacity: 0, y: yOffset, filter: "blur(6px)" }}
      animate={inView ? { opacity: 1, y: 0, filter: "blur(0px)" } : {}}
      transition={{ duration: 0.55, delay, ease: [0.21, 0.47, 0.32, 0.98] }}
      className={className}
    >
      {children}
    </motion.div>
  );
}
