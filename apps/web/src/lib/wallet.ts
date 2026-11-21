"use client";

import {createPublicClient, createWalletClient, custom, http, type Abi, type Address, type EIP1193Provider, type Hex} from "viem";
import {avalancheFuji, foundry} from "viem/chains";
import {post} from "./api";

declare global {interface Window {ethereum?: EIP1193Provider}}

export async function requestWallet(chainId: number) {
  if (!window.ethereum) throw new Error("Chưa tìm thấy ví trình duyệt. Hãy mở bằng trình duyệt có ví hỗ trợ Ethereum.");
  const chain = chainId === 43113 ? avalancheFuji : chainId === 31337 ? foundry : null;
  if (!chain) throw new Error("Mạng này chưa được hỗ trợ.");
  const client = createWalletClient({chain, transport: custom(window.ethereum)});
  const [address] = await client.requestAddresses();
  if (!address) throw new Error("Chưa chọn tài khoản ví.");
  try {await client.switchChain({id: chain.id});}
  catch (error) {
    const value = error as {code?: number; cause?: {code?: number}};
    if (value.code === 4902 || value.cause?.code === 4902) {
      await client.addChain({chain: {...chain, rpcUrls: {default: {http: [chain.id === 31337 ? "http://127.0.0.1:18545" : "https://api.avax-test.network/ext/bc/C/rpc"]}}}});
      await client.switchChain({id: chain.id});
    } else throw error;
  }
  return address;
}

export async function connectWallet(chainId: number) {
  const address = await requestWallet(chainId);
  const challenge = await post<{id: string; typed_data: Record<string, unknown>}>("/v1/wallet/challenge", {address});
  const signature = await signIntent(address, challenge.typed_data);
  await post("/v1/wallet/verify", {challenge_id: challenge.id, signature});
  return address;
}

export async function signIntent(address: Address, typed: Record<string, unknown>): Promise<Hex> {
  if (!window.ethereum) throw new Error("Ví đã ngắt kết nối.");
  const accounts = await window.ethereum.request({method: "eth_accounts"});
  if (!accounts.some(value => value.toLowerCase() === address.toLowerCase())) throw new Error("Hãy chọn đúng tài khoản ví đã liên kết.");
  return window.ethereum.request({method: "eth_signTypedData_v4", params: [address, JSON.stringify(typed)]});
}

export async function sendContract(chainId: number, account: Address, address: Address, abi: Abi, functionName: string, args: readonly unknown[] = []) {
  if (!window.ethereum) throw new Error("Chưa kết nối ví.");
  const chain = chainId === 43113 ? avalancheFuji : foundry;
  const wallet = createWalletClient({chain, account, transport: custom(window.ethereum)});
  if (await wallet.getChainId() !== chainId) throw new Error("Ví đang ở mạng khác. Hãy kết nối lại đúng mạng.");
  const accounts = await wallet.getAddresses();
  if (!accounts.some(value => value.toLowerCase() === account.toLowerCase())) throw new Error("Tài khoản ví đã thay đổi. Hãy kết nối lại ví đã liên kết.");
  const publicClient = createPublicClient({chain, transport: http(chainId === 31337 ? "http://127.0.0.1:18545" : "https://api.avax-test.network/ext/bc/C/rpc")});
  const {request} = await publicClient.simulateContract({account, address, abi, functionName, args});
  const hash = await wallet.writeContract(request);
  const receipt = await publicClient.waitForTransactionReceipt({hash, timeout: 60000});
  if (receipt.status !== "success") throw new Error("Giao dịch chưa thành công. Trạng thái quỹ chưa thay đổi.");
  return hash;
}

export function walletError(error: unknown): Error {
  const value = error as {code?: number; cause?: {code?: number}; message?: string; shortMessage?: string};
  if (value.code === 4001 || value.cause?.code === 4001 || /reject|denied/i.test(value.message || "")) {
    return new Error("Bạn đã hủy yêu cầu trong ví. Chưa có giao dịch được gửi.");
  }
  return new Error(value.shortMessage || value.message || "Chưa hoàn tất thao tác ví. Vui lòng thử lại.");
}
