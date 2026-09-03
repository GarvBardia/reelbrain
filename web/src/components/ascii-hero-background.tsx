"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";
import { AsciiEffect } from "three/examples/jsm/effects/AsciiEffect.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

import { cn } from "@/lib/utils";

/**
 * Ambient ASCII-rendered mycelium network for the hero background.
 *
 * A free reimplementation of the effect Skiper UI's paid "skiper14" sells,
 * built on three's own AsciiEffect (MIT, ships inside the three package we
 * already depend on). No paid dependency, no licence key.
 *
 * ON THE 3D MODEL -- read before adding one:
 * The brief pointed at Sketchfab's "CC0 - Mushroom 2". Two things came back
 * when that was actually checked rather than assumed:
 *   1. api.sketchfab.com/v3/models/<uid>/download returns 401 -- downloads are
 *      OAuth-gated, so the file cannot be fetched without an account.
 *   2. Its own API metadata reports license "CC Attribution", NOT CC0, despite
 *      "CC0" being in the model's title. That is a real licensing discrepancy,
 *      and CC-BY carries an attribution obligation CC0 does not.
 * So no model is bundled. The mycelium is generated procedurally instead,
 * which is not merely a fallback -- it is a better fit here:
 *   - Zero licensing surface. Nothing to attribute, nothing to get wrong.
 *   - Thematically closer. The brief itself notes a branching network reads as
 *     "mycelium" where a single mushroom cap does not; this generates exactly
 *     that -- filaments branching from a core with nodes at the junctions.
 *   - Far cheaper. A few hundred KB of geometry built at runtime instead of a
 *     multi-MB .glb over the wire, which matters for the perf requirement.
 * `modelPath` is still honoured: point it at a .glb in public/models/ and it
 * loads that instead, no code change needed.
 */

export type AsciiHeroBackgroundProps = {
  /** Optional .glb under public/models/. Omitted -> procedural mycelium. */
  modelPath?: string;
  /** Ramp from darkest to brightest. Order matters. */
  asciiChars?: string;
  /** CSS colour behind the glyphs. "transparent" lets the hero show through. */
  backgroundColor?: string;
  /** Glyph colour. Defaults to the nebula palette's violet. */
  textColor?: string;
  /** Glyph size in px. Also sets the grid density -- the two are the same
   *  knob in AsciiEffect; see resolutionForFontSize. Larger = cheaper. */
  fontSize?: number;
  /** Radians per second around Y. Deliberately slow -- this is wallpaper. */
  rotationSpeed?: number;
  className?: string;
};

/**
 * fontSize and resolution are NOT independent -- AsciiEffect derives one from
 * the other: `fFontSize = (2 / fResolution) * iScale` (AsciiEffect.js:123).
 * So the grid's column count and the glyph size are locked together, and
 * setting font-size by hand desynchronises them: forcing 9px onto a grid
 * built for 18px rendered the glyphs at half the container's width and left
 * the right half of the hero empty. Caught on screen, not in review.
 *
 * The fontSize prop therefore DRIVES resolution rather than overriding the
 * result, which keeps the grid filling its box at any size the caller asks
 * for. Bigger font -> coarser grid -> fewer glyphs -> less DOM per frame,
 * which is also the main performance lever here, since every rendered frame
 * rebuilds the table's innerHTML.
 */
const resolutionForFontSize = (fontSize: number) => 2 / fontSize;

/** ~18fps. The rotation is slow enough that 60fps buys nothing visible, and
 *  each frame costs a full innerHTML rebuild of the glyph table. */
const FRAME_INTERVAL_MS = 55;

/** Below this width the effect is skipped entirely -- see shouldRender(). */
const MIN_WIDTH = 768;

/**
 * Is this device worth rendering an ASCII scene on?
 *
 * Skipped for: reduced-motion users (this thing never stops moving), narrow
 * viewports (the hero text needs the whole screen on a phone, and the glyph
 * grid is unreadable at that size anyway), and low-core/low-memory devices,
 * where a per-frame DOM rebuild is exactly the wrong thing to be doing.
 * navigator.deviceMemory is Chromium-only; absent elsewhere, so it only ever
 * rules a device OUT when it explicitly reports being small.
 */
function shouldRender(): boolean {
  if (typeof window === "undefined") return false;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return false;
  if (window.innerWidth < MIN_WIDTH) return false;
  const cores = navigator.hardwareConcurrency;
  if (typeof cores === "number" && cores > 0 && cores <= 4) return false;
  const mem = (navigator as any).deviceMemory;
  if (typeof mem === "number" && mem > 0 && mem <= 4) return false;
  return true;
}

/**
 * Builds the mycelium: filaments branching outward from a core, thinning at
 * each generation, with a node bulb at every junction.
 *
 * Tubes rather than lines on purpose. AsciiEffect picks its glyphs from the
 * LUMINANCE of the rendered frame, so geometry has to be shaded to register
 * at all -- a THREE.Line renders a hairline with almost no tonal range and
 * would asciify into near-nothing.
 *
 * Seeded (mulberry32) rather than Math.random so the same shape is produced
 * on every mount and across reloads. A background that silently rearranges
 * itself between visits reads as instability, not life.
 */
function buildMycelium(): THREE.Group {
  const group = new THREE.Group();
  let seed = 0x9e3779b9;
  const rand = () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };

  const material = new THREE.MeshStandardMaterial({
    color: 0xffffff,
    roughness: 0.45,
    metalness: 0.05,
  });

  const tubes: THREE.TubeGeometry[] = [];
  const nodes: THREE.Matrix4[] = [];

  const grow = (
    origin: THREE.Vector3,
    direction: THREE.Vector3,
    length: number,
    radius: number,
    depth: number,
  ) => {
    if (depth === 0 || radius < 0.012) return;

    // Curve the filament instead of running it straight: two jittered control
    // points are what make it read as grown rather than drafted.
    const mid = origin
      .clone()
      .add(direction.clone().multiplyScalar(length * 0.5))
      .add(
        new THREE.Vector3(rand() - 0.5, rand() - 0.5, rand() - 0.5).multiplyScalar(length * 0.35),
      );
    const end = origin
      .clone()
      .add(direction.clone().multiplyScalar(length))
      .add(
        new THREE.Vector3(rand() - 0.5, rand() - 0.5, rand() - 0.5).multiplyScalar(length * 0.25),
      );

    const curve = new THREE.CatmullRomCurve3([origin.clone(), mid, end]);
    tubes.push(new THREE.TubeGeometry(curve, 8, radius, 5, false));
    nodes.push(new THREE.Matrix4().makeTranslation(end.x, end.y, end.z));

    const branches = depth > 2 ? 3 : 2;
    for (let i = 0; i < branches; i++) {
      const next = direction
        .clone()
        .add(new THREE.Vector3(rand() - 0.5, rand() - 0.5, rand() - 0.5).multiplyScalar(1.15))
        .normalize();
      grow(end, next, length * (0.62 + rand() * 0.16), radius * 0.62, depth - 1);
    }
  };

  // Six roots off the core, spread over a sphere so it reads as a volume
  // rather than a flat spray when it rotates.
  for (let i = 0; i < 6; i++) {
    const dir = new THREE.Vector3(rand() - 0.5, rand() - 0.5, rand() - 0.5).normalize();
    grow(new THREE.Vector3(0, 0, 0), dir, 1.05, 0.055, 4);
  }

  for (const geometry of tubes) group.add(new THREE.Mesh(geometry, material));

  // Junction bulbs, instanced: one draw call for all of them.
  const bulb = new THREE.SphereGeometry(0.05, 8, 6);
  const bulbs = new THREE.InstancedMesh(bulb, material, nodes.length);
  nodes.forEach((m, i) => bulbs.setMatrixAt(i, m));
  bulbs.instanceMatrix.needsUpdate = true;
  group.add(bulbs);

  // A dense core so the centre of the rotation has mass to shade.
  group.add(new THREE.Mesh(new THREE.SphereGeometry(0.17, 16, 12), material));

  return group;
}

export function AsciiHeroBackground({
  modelPath,
  asciiChars = " .:-+*=%@#",
  backgroundColor = "transparent",
  textColor = "#8b5cf6",
  fontSize = 16,
  rotationSpeed = 0.12,
  className,
}: AsciiHeroBackgroundProps) {
  const mountRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount || !shouldRender()) return;

    let disposed = false;
    let frame = 0;
    let lastDraw = 0;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
    // Pulled back so the network sits inside the frame with empty space
    // around it. Filling the viewport edge to edge leaves no unlit area, and
    // without unlit area there is no silhouette -- just a uniform glyph field.
    camera.position.set(0, 0, 4.6);

    scene.add(new THREE.AmbientLight(0xffffff, 0.55));
    const key = new THREE.DirectionalLight(0xffffff, 2.1);
    key.position.set(2.5, 2, 3);
    scene.add(key);
    const rim = new THREE.DirectionalLight(0xffffff, 0.9);
    rim.position.set(-2.5, -1.5, -2);
    scene.add(rim);

    const renderer = new THREE.WebGLRenderer({ antialias: false, alpha: true });
    // DPR is pinned to 1: the output is a coarse glyph grid, so rendering the
    // source frame at 2x on a retina display costs 4x the pixels for zero
    // visible difference once it has been asciified.
    renderer.setPixelRatio(1);

    // invert:false is load-bearing for an overlay. AsciiEffect maps luminance
    // to the character ramp, and with invert:true the DARK background maps to
    // the ramp's densest glyph -- so the hero filled with a solid "#" wall and
    // the mycelium punched a hole in it, the exact inverse of what an ambient
    // overlay wants. Left alone, empty space maps to " " (invisible over the
    // hero) and only the lit filaments draw glyphs.
    const effect = new AsciiEffect(renderer, asciiChars, {
      invert: false,
      resolution: resolutionForFontSize(fontSize),
    });
    effect.domElement.style.color = textColor;
    effect.domElement.style.backgroundColor = backgroundColor;
    effect.domElement.style.pointerEvents = "none";

    const subject = new THREE.Group();
    scene.add(subject);

    // No typography override here on purpose -- see resolutionForFontSize.
    // AsciiEffect recomputes font-size, line-height and letter-spacing from
    // the resolution on every setSize, and they have to stay consistent with
    // the column count or the grid stops filling its container.
    const resize = () => {
      const { width, height } = mount.getBoundingClientRect();
      if (width === 0 || height === 0) return;
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      effect.setSize(width, height);
    };

    mount.appendChild(effect.domElement);
    resize();

    let cleanupModel: (() => void) | undefined;
    if (modelPath) {
      new GLTFLoader().load(
        modelPath,
        (gltf) => {
          if (disposed) return;
          // Normalise whatever was loaded into the same ~2-unit box the
          // procedural version occupies, so the camera framing holds for any
          // model dropped in later.
          const box = new THREE.Box3().setFromObject(gltf.scene);
          const size = box.getSize(new THREE.Vector3());
          const centre = box.getCenter(new THREE.Vector3());
          const scale = 2.2 / Math.max(size.x, size.y, size.z || 1);
          gltf.scene.position.sub(centre);
          gltf.scene.scale.setScalar(scale);
          subject.add(gltf.scene);
        },
        undefined,
        () => {
          // A missing or broken model must not leave an empty hero: fall back
          // to the procedural network rather than rendering nothing.
          if (!disposed && subject.children.length === 0) subject.add(buildMycelium());
        },
      );
    } else {
      const mycelium = buildMycelium();
      subject.add(mycelium);
      cleanupModel = () => {
        mycelium.traverse((o) => {
          const mesh = o as THREE.Mesh;
          if (mesh.geometry) mesh.geometry.dispose();
        });
      };
    }

    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : undefined;
    ro?.observe(mount);
    window.addEventListener("resize", resize);

    /**
     * Skip rendering while the hero is scrolled past -- no point rebuilding a
     * glyph table nobody is looking at.
     *
     * This was an IntersectionObserver first. It is a rect test now because
     * IO here has a latch failure mode: its callback is asynchronous, so the
     * first one can land after the opening frame and report
     * isIntersecting:false while the element is still being laid out -- and
     * since a hero at the top of the page never moves again, IO has no reason
     * to fire a second time, leaving the flag stuck off forever. A rect test
     * is recomputed from scratch on every tick and so cannot latch. At ~18fps
     * getBoundingClientRect costs nothing next to the innerHTML rebuild it
     * guards.
     *
     * Note for anyone re-testing this: a frozen glyph table in an automated
     * browser is most likely NOT this code. Chrome throttles rAF to zero in
     * background tabs, so a driver that never foregrounds the tab will see
     * exactly one frame and then stillness (document.hidden === true is the
     * tell). That is what an earlier "it renders once and freezes" reading
     * here turned out to be.
     */
    const onScreen = () => {
      const rect = mount.getBoundingClientRect();
      return rect.bottom > 0 && rect.top < window.innerHeight;
    };

    const clock = new THREE.Clock();
    const tick = (now: number) => {
      frame = requestAnimationFrame(tick);
      if (now - lastDraw < FRAME_INTERVAL_MS) return;
      if (!onScreen()) return;
      lastDraw = now;
      subject.rotation.y += rotationSpeed * clock.getDelta();
      subject.rotation.x = Math.sin(subject.rotation.y * 0.35) * 0.18;
      effect.render(scene, camera);
    };
    frame = requestAnimationFrame(tick);

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      ro?.disconnect();
      window.removeEventListener("resize", resize);
      cleanupModel?.();
      scene.traverse((o) => {
        const mesh = o as THREE.Mesh;
        if (mesh.geometry) mesh.geometry.dispose();
        const mat = mesh.material as THREE.Material | THREE.Material[] | undefined;
        if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
        else mat?.dispose();
      });
      renderer.dispose();
      if (effect.domElement.parentNode === mount) mount.removeChild(effect.domElement);
    };
  }, [modelPath, asciiChars, backgroundColor, textColor, fontSize, rotationSpeed]);

  return <div ref={mountRef} aria-hidden className={cn("h-full w-full", className)} />;
}

export default AsciiHeroBackground;
