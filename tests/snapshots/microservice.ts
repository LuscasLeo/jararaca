
/* eslint-disable */

// @ts-nocheck

// noinspection JSUnusedGlobalSymbols

import { HttpService, HttpBackend, HttpBackendRequest, ResponseType, createClassQueryHooks , createClassMutationHooks, createClassInfiniteQueryHooks, paginationModelByFirstArgPaginationFilter, recursiveCamelToSnakeCase } from "@jararaca/core";

function makeFormData(data: Record<string, any>): FormData {
  const formData = new FormData();
  for (const key in data) {
    const value = data[key];
    for (const v of genFormDataValue(value)) {
      formData.append(key, v);
    }
  }
  return formData;
}

function* genFormDataValue(value: any): any {
  if (Array.isArray(value)) {
    // Stringify arrays as JSON
    for (const item of value) {
      // formData.append(`${key}`, item);
      yield* genFormDataValue(item);
    }
  } else if (typeof value === "object" && value.constructor === Object) {
    // Stringify plain objects as JSON
    // formData.append(key, JSON.stringify(value));
    yield JSON.stringify(
      recursiveCamelToSnakeCase(value)
    );
  } else {
    // For primitives (string, number, boolean), append as-is
    yield value;
  }
}

export type WebSocketMessageMap = {
	"ping": Ping;
}
export interface BinaryIO {
}
export interface Collection extends Container, Iterable, Sized {
}
export enum Color {
	BLUE = "blue",
 	RED = "red",
}
export interface Container {
}
export interface CreateItem {
  color: Color;
  name: string;
}
export interface Exposed {
  value: number;
}
export interface Headers extends Mapping {
  raw: unknown;
}
export interface Item {
  color: Color;
  id: string;
  kind: "a" | "b";
  name: string;
  note?: string | null;
  tags?: Array<string>;
}
export interface Iterable {
}
export interface Mapping extends Collection {
}
export interface Page<T> {
  items: Array<T>;
  total: number;
}
export interface ProfileInput {
  age?: number;
  name: string;
  nick?: string | null;
}
export interface ProfileOutput {
  age: number;
  name: string;
  nick: string | null;
}
export interface Sized {
}
export interface WebSocketMessageBase {
}
export interface WebSocketMessageWrapper<INHERITS_WS_MESSAGE> {
  MESSAGE_ID: string;
  message: INHERITS_WS_MESSAGE;
}
export class ItemsController extends HttpService {
	async bothItem(x_token: string, body: CreateItem): Promise<Item> {
		const response = await this.httpBackend.request<Item>({ 
			method: "POST",
			path: `/items/both`,
			pathParams: {
			},
			headers: {
				"x_token": x_token,
			},
			query: {
			},
			body: body
		});
		return response;
	}
	async createItem(x_token: string, body: CreateItem): Promise<Item> {
		const response = await this.httpBackend.request<Item>({ 
			method: "POST",
			path: `/items`,
			pathParams: {
			},
			headers: {
				"x_token": x_token,
			},
			query: {
			},
			body: body
		});
		return response;
	}
	async deleteItem(x_token: string, item_id: string): Promise<null> {
		const response = await this.httpBackend.request<null>({ 
			method: "DELETE",
			path: `/items/:item_id`,
			pathParams: {
				"item_id": item_id,
			},
			headers: {
				"x_token": x_token,
			},
			query: {
			},
			body: undefined
		});
		return response;
	}
	async getItem(x_token: string, item_id: string): Promise<Item> {
		const response = await this.httpBackend.request<Item>({ 
			method: "GET",
			path: `/items/:item_id`,
			pathParams: {
				"item_id": item_id,
			},
			headers: {
				"x_token": x_token,
			},
			query: {
			},
			body: undefined
		});
		return response;
	}
	async listItems(x_token: string, color: Color | null): Promise<Page<Item>> {
		const response = await this.httpBackend.request<Page<Item>>({ 
			method: "GET",
			path: `/items`,
			pathParams: {
			},
			headers: {
				"x_token": x_token,
			},
			query: {
				"color": color,
			},
			body: undefined
		});
		return response;
	}
	async patchProfile(x_token: string, body: ProfileInput): Promise<ProfileOutput> {
		const response = await this.httpBackend.request<ProfileOutput>({ 
			method: "PATCH",
			path: `/items/profile`,
			pathParams: {
			},
			headers: {
				"x_token": x_token,
			},
			query: {
			},
			body: body
		});
		return response;
	}
	async searchItems(x_token: string, body: CreateItem): Promise<Page<Item>> {
		const response = await this.httpBackend.request<Page<Item>>({ 
			method: "PATCH",
			path: `/items/search`,
			pathParams: {
			},
			headers: {
				"x_token": x_token,
			},
			query: {
			},
			body: body
		});
		return response;
	}
	async updateItem(x_token: string, item_id: string, body: CreateItem): Promise<Item> {
		const response = await this.httpBackend.request<Item>({ 
			method: "PUT",
			path: `/items/:item_id`,
			pathParams: {
				"item_id": item_id,
			},
			headers: {
				"x_token": x_token,
			},
			query: {
			},
			body: body
		});
		return response;
	}
	async upload(x_token: string, file: File, title: string): Promise<{[key: string]: number}> {
		const response = await this.httpBackend.request<{[key: string]: number}>({ 
			method: "POST",
			path: `/items/upload`,
			pathParams: {
			},
			headers: {
				"x_token": x_token,
			},
			query: {
			},
			body: makeFormData({
				"file": file,
				"title": title,
			})
		});
		return response;
	}
}
export const itemsController = {
	...createClassQueryHooks(ItemsController,
		"getItem",
		"searchItems",
	),
	...createClassInfiniteQueryHooks(ItemsController, {
		"searchItems": paginationModelByFirstArgPaginationFilter(),
	}),
	...createClassMutationHooks(ItemsController,
		"createItem",
	),
	queries: {
		...createClassQueryHooks(ItemsController,
			"bothItem",
		),
	},
	mutations: {
		...createClassMutationHooks(ItemsController,
			"bothItem",
		),
	},
};
export class WsController extends HttpService {
	async status(): Promise<"ok"> {
		const response = await this.httpBackend.request<"ok">({ 
			method: "GET",
			path: `/ws/status`,
			pathParams: {
			},
			headers: {
			},
			query: {
			},
			body: undefined
		});
		return response;
	}
}
export interface Ping extends Omit<WebSocketMessageBase, "MESSAGE_ID"> {
  seq: number;
}
export class RichController extends HttpService {
	async mixed(tenant: string, trace: string, upload: File, payload: CreateItem, search: string, scope_id: number, plain_path: number, plain_query: string, plain_path: number, profile: ProfileInput, item: CreateItem, q: string, flag: boolean, files: Array<File>, token: string, session: string, cookie_value: string, header_value: string, profile_query: Profile, profile_path: Profile, profile_body: Profile, form_value: number): Promise<Array<ProfileOutput>> {
		const response = await this.httpBackend.request<Array<ProfileOutput>>({ 
			method: "POST",
			path: `/rich/:scope_id/:plain_path/mixed`,
			pathParams: {
				"plain_path": plain_path,
				"plain_path": plain_path,
				"profile_path": profile_path,
				"scope_id": scope_id,
			},
			headers: {
				"header_value": header_value,
				"tenant": tenant,
				"token": token,
			},
			query: {
				"flag": flag,
				"plain_query": plain_query,
				"profile_query": profile_query,
				"q": q,
				"search": search,
			},
			body: makeFormData({
				"upload": upload,
				"files": files,
				"form_value": form_value,
				...payload,
			})
		});
		return response;
	}
}
