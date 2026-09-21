"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";

import { cn } from "@/lib/utils";
import {
  BLOOM_RADIUS,
  BLOOM_STRENGTH,
  BLOOM_THRESHOLD,
  SCENE_BACKGROUND,
  buildDust,
  buildInstancedPoints,
  canRenderSphere,
  makeRand,
} from "./sphere-core";

/**
 * The STATIC decorative hero sphere.
 *
 * Deliberately inert: no scroll listener, no progress input, no camera
 * dolly, no rush-out, no handoff. The scroll-driven version this replaces
 * was reported as janky, and the response is to delete the mechanism
 * rather than to tune it -- a hero visual that reacts to nothing cannot
 * feel janky, and the interactive version of this sphere now lives behind
 * an explicit "View graph" action instead of being something you fall
 * into by scrolling.
 *
 * The only motion left is a slow Y rotation, the same ambient wallpaper
 * rotation the two hero backgrounds before this one used.
 *
 * This renders DUST ONLY -- no real data, no interactivity. It is
 * decoration on the marketing page and nothing more; the real reel nodes
 * live in sphere-graph.tsx behind /graph. Keeping the two separate is what
 * lets the homepage stay cheap (no API call needed for the hero) while the
 * graph view pays for what it actually uses.
 */

export type HeroSphereProps = {
  /** Decorative particle count. Same 11,000 the previous hero used. */
  particleCount?: number;
  /** Radians per second around Y. Wallpaper-slow on purpose. */
  rotationSpeed?: number;
  className?: string;
};

export function HeroSphere({
  particleCount = 11000,
  rotationSpeed = 0.05,
  className,
}: HeroSphereProps) {
  const mountRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount || !canRenderSphere()) return;

    let frame = 0;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(SCENE_BACKGROUND);

    const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 100);
    camera.position.set(0, 0, 4.4);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

    const composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    const bloomPass = new UnrealBloomPass(
      new THREE.Vector2(1, 1),
      BLOOM_STRENGTH,
      BLOOM_RADIUS,
      BLOOM_THRESHOLD,
    );
    composer.addPass(bloomPass);

    const rand = makeRand(0x51ed270b);
    const dust = buildDust(rand, particleCount);
    const positions = dust.map((d) => d.position);
    const colors = dust.map((d) => d.color);
    const scales = dust.map((d) => d.scale);

    // Two layers sharing positions and colours: a crisp core dot and a
    // larger, very transparent halo. On white this halo is what produces
    // the glow bloom cannot -- see sphere-core's module docstring.
    const core = buildInstancedPoints(positions, colors, scales, 0.035, 1, 1);
    const halo = buildInstancedPoints(positions, colors, scales, 0.09, 0.13, 0);

    const subject = new THREE.Group();
    subject.add(halo.mesh);
    subject.add(core.mesh);
    scene.add(subject);

    const resize = () => {
      const { width, height } = mount.getBoundingClientRect();
      if (width === 0 || height === 0) return;
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height);
      composer.setSize(width, height);
      bloomPass.setSize(width, height);
    };

    mount.appendChild(renderer.domElement);
    resize();

    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : undefined;
    ro?.observe(mount);
    window.addEventListener("resize", resize);

    // Rect test rather than IntersectionObserver: IO's async first callback
    // can latch a wrong isIntersecting:false for an element that never
    // scrolls again, which is exactly what a hero is.
    const onScreen = () => {
      const rect = mount.getBoundingClientRect();
      return rect.bottom > 0 && rect.top < window.innerHeight;
    };

    const clock = new THREE.Clock();
    const tick = () => {
      frame = requestAnimationFrame(tick);
      if (!onScreen()) return;
      subject.rotation.y += rotationSpeed * Math.min(clock.getDelta(), 0.1);
      composer.render();
    };
    frame = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(frame);
      ro?.disconnect();
      window.removeEventListener("resize", resize);
      core.geometry.dispose();
      core.material.dispose();
      halo.geometry.dispose();
      halo.material.dispose();
      composer.dispose();
      renderer.dispose();
      if (renderer.domElement.parentNode === mount) mount.removeChild(renderer.domElement);
    };
  }, [particleCount, rotationSpeed]);

  return <div ref={mountRef} aria-hidden className={cn("h-full w-full", className)} />;
}

export default HeroSphere;
