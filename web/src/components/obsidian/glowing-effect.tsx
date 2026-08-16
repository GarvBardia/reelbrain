// obsidianui.dev — "Glowing Effect". Vendored; see the note in light-lines.tsx
// about why these live in the repo rather than node_modules.
// Fetched from https://obsidianui.dev/r/glowing-effect.json
//
// A cursor-PROXIMITY border glow: it tracks the pointer's angle relative to the
// element and lights the border edge nearest the cursor. Ships a
// `variant="white"` specifically for light backgrounds, which is what makes it
// the right pick here over glow-border-card (that one hardcodes bg-neutral-900
// and neon presets built for dark UIs). Unmodified.
"use client";

import { memo, useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

interface GlowingEffectProps {
    blur?: number;
    inactiveZone?: number;
    proximity?: number;
    spread?: number;
    variant?: "default" | "white";
    glow?: boolean;
    className?: string;
    disabled?: boolean;
    movementDuration?: number;
    borderWidth?: number;
}

const GlowingEffect = memo(
    ({
        blur = 0,
        inactiveZone = 0.01,
        proximity = 100,
        spread = 40,
        variant = "default",
        glow = true,
        className,
        movementDuration = 0.15,
        borderWidth = 2,
        disabled = false,
    }: GlowingEffectProps) => {
        const containerRef = useRef<HTMLDivElement>(null);
        const angleRef = useRef(0);
        const [angle, setAngle] = useState(0);
        const [isActive, setIsActive] = useState(false);
        const animationRef = useRef<number>();

        const handlePointerMove = useCallback(
            (e: PointerEvent) => {
                if (disabled || !containerRef.current) return;

                const element = containerRef.current;
                const rect = element.getBoundingClientRect();
                const mouseX = e.clientX;
                const mouseY = e.clientY;

                const centerX = rect.left + rect.width / 2;
                const centerY = rect.top + rect.height / 2;

                // Check proximity
                const isWithinProximity =
                    mouseX > rect.left - proximity &&
                    mouseX < rect.right + proximity &&
                    mouseY > rect.top - proximity &&
                    mouseY < rect.bottom + proximity;

                if (!isWithinProximity) {
                    setIsActive(false);
                    return;
                }

                setIsActive(true);

                // Calculate target angle
                const targetAngle = Math.atan2(mouseY - centerY, mouseX - centerX) * (180 / Math.PI) + 90;

                // Smooth angle interpolation
                const currentAngle = angleRef.current;
                let diff = targetAngle - currentAngle;

                // Handle 360-degree wrap
                if (diff > 180) diff -= 360;
                if (diff < -180) diff += 360;

                const newAngle = currentAngle + diff * 0.15; // Smooth factor
                angleRef.current = newAngle;
                setAngle(newAngle);
            },
            [disabled, proximity]
        );

        useEffect(() => {
            if (disabled) return;

            const handleMove = (e: PointerEvent) => {
                if (animationRef.current) cancelAnimationFrame(animationRef.current);
                animationRef.current = requestAnimationFrame(() => handlePointerMove(e));
            };

            document.addEventListener("pointermove", handleMove, { passive: true });
            return () => {
                document.removeEventListener("pointermove", handleMove);
                if (animationRef.current) cancelAnimationFrame(animationRef.current);
            };
        }, [handlePointerMove, disabled]);

        const gradientColors =
            variant === "white"
                ? "rgba(255,255,255,0.9), rgba(255,255,255,0.5), rgba(255,255,255,0.1), transparent"
                : "#ff6b6b, #feca57, #48dbfb, #ff9ff3, #54a0ff, #5f27cd, #ff6b6b";

        return (
            <div
                ref={containerRef}
                className={cn(
                    "pointer-events-none absolute inset-0 rounded-[inherit] overflow-visible",
                    className
                )}
            >
                {/* Main glow border with smooth transition */}
                <div
                    className="absolute inset-0 rounded-[inherit] transition-opacity duration-300"
                    style={{
                        background: `conic-gradient(from ${angle}deg at 50% 50%, ${gradientColors})`,
                        padding: `${borderWidth}px`,
                        WebkitMask: "linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0)",
                        WebkitMaskComposite: "xor",
                        maskComposite: "exclude",
                        opacity: isActive ? 1 : 0,
                        filter: blur > 0 ? `blur(${blur}px)` : undefined,
                    }}
                />

                {/* Soft outer glow for premium effect */}
                {glow && (
                    <div
                        className="absolute -inset-1 rounded-[inherit] transition-opacity duration-500"
                        style={{
                            background: `conic-gradient(from ${angle}deg at 50% 50%, ${gradientColors})`,
                            filter: "blur(12px)",
                            opacity: isActive ? 0.6 : 0,
                        }}
                    />
                )}

                {/* Extra soft ambient glow */}
                {glow && (
                    <div
                        className="absolute -inset-3 rounded-[inherit] transition-opacity duration-700"
                        style={{
                            background: `conic-gradient(from ${angle}deg at 50% 50%, ${gradientColors})`,
                            filter: "blur(25px)",
                            opacity: isActive ? 0.3 : 0,
                        }}
                    />
                )}
            </div>
        );
    }
);

GlowingEffect.displayName = "GlowingEffect";

export { GlowingEffect };
