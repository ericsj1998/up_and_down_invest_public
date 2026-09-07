/**
 * Material Tailwind 재수출 — **타입만 느슨하게** 한다 (T220).
 *
 * 🔴 MT 2.1 의 타입은 `@types/react` 18.2.42+ 와 만나면 모든 컴포넌트에
 *    `placeholder` · `onPointerEnterCapture` · `onPointerLeaveCapture` 를 **필수**로 요구한다
 *    (MT 이슈 #528 — `Omit` 타이핑 결함). 화면마다 `placeholder={undefined}` 를 세 개씩 붙이는
 *    대신 여기서 한 번 벗긴다. 런타임 객체는 그대로다 — 이 파일은 타입 캐스트만 한다.
 *
 * ⚠️ 새 MT 컴포넌트를 쓰려면 **여기에 한 줄 추가**한다. 화면에서 `@material-tailwind/react` 를
 *    직접 import 하지 않는다 — 그러면 위 세 프롭이 다시 필수가 되어 tsc 가 막는다.
 */
import type { ComponentType } from "react";
import * as MT from "@material-tailwind/react";

// ⚠️ `onResize`·`onResizeCapture` 도 같은 결함으로 필수가 된다 (실측 2026-09-05 · @types/react 18.3.12).
type Nuisance =
  | "placeholder"
  | "onPointerEnterCapture"
  | "onPointerLeaveCapture"
  | "onResize"
  | "onResizeCapture";

type Loose<T> = T extends ComponentType<infer P> ? ComponentType<Omit<P, Nuisance>> : T;

function loose<T>(component: T): Loose<T> {
  return component as unknown as Loose<T>;
}

export const ThemeProvider = loose(MT.ThemeProvider);
export const Typography = loose(MT.Typography);
export const Card = loose(MT.Card);
export const CardHeader = loose(MT.CardHeader);
export const CardBody = loose(MT.CardBody);
export const CardFooter = loose(MT.CardFooter);
export const Button = loose(MT.Button);
export const IconButton = loose(MT.IconButton);
export const Chip = loose(MT.Chip);
export const Tooltip = loose(MT.Tooltip);
export const Navbar = loose(MT.Navbar);
export const Progress = loose(MT.Progress);
export const Alert = loose(MT.Alert);
export const Input = loose(MT.Input);
export const Select = loose(MT.Select);
export const Option = loose(MT.Option);
export const Dialog = loose(MT.Dialog);
export const DialogHeader = loose(MT.DialogHeader);
export const DialogBody = loose(MT.DialogBody);
export const DialogFooter = loose(MT.DialogFooter);
export const Tabs = loose(MT.Tabs);
export const TabsHeader = loose(MT.TabsHeader);
export const Tab = loose(MT.Tab);
