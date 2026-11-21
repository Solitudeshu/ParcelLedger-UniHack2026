// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/// @notice Test funds ONLY. Deployments are deliberately restricted to Fuji/local chains.
contract DemoToken is ERC20 {
    constructor() ERC20("Kien Chung Test USD - NO VALUE", "kcTEST") {
        require(block.chainid == 43113 || block.chainid == 31337, "TESTNET_ONLY");
    }
    function decimals() public pure override returns (uint8) { return 6; }
    function faucet() external { _mint(msg.sender, 1000 * 10**6); }
}
