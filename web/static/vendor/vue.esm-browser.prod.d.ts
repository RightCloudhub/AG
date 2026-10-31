/** Typed subset of the pinned Vue 3.5.13 API used by this zero-build application. */
export interface Ref<T> { value: T }
export interface Component {
  name?: string;
  props?: Record<string, unknown>;
  setup?(props: Record<string, unknown>): object;
  template: string;
}
export interface App { mount(selector: string): unknown; component(name: string, component: Component): App }
export function ref<T>(value: T): Ref<T>;
export function shallowRef<T>(value: T): Ref<T>;
export function computed<T>(getter: () => T): Readonly<Ref<T>>;
export function nextTick(callback: () => void): Promise<void>;
export function onMounted(callback: () => unknown): void;
export function onUnmounted(callback: () => unknown): void;
export function createApp(component: Component): App;
