// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Ownable2Step} from "@openzeppelin/contracts/access/Ownable2Step.sol";
import {ShipmentEscrow} from "./ShipmentEscrow.sol";

contract EscrowFactory is Ownable2Step {
    mapping(address => bool) public allowedTokens;
    mapping(address => bool) public allowedArbitrators;
    mapping(bytes32 => address) public cases;
    bool public intakePaused;

    error InvalidCase();
    error IntakePaused();
    event CaseCreated(bytes32 indexed caseId, address indexed escrow, address indexed merchant, address buyer);
    event TokenPermission(address indexed token, bool allowed);
    event ArbitratorPermission(address indexed arbitrator, bool allowed);
    event IntakePauseChanged(bool paused);

    constructor(address initialOwner) Ownable(initialOwner) {}

    function setAllowedToken(address token, bool allowed) external onlyOwner {
        if (allowed && token.code.length == 0) revert InvalidCase();
        allowedTokens[token] = allowed;
        emit TokenPermission(token, allowed);
    }
    function setAllowedArbitrator(address arbitrator, bool allowed) external onlyOwner {
        if (allowed && arbitrator.code.length == 0) revert InvalidCase();
        allowedArbitrators[arbitrator] = allowed;
        emit ArbitratorPermission(arbitrator, allowed);
    }
    function setIntakePaused(bool paused) external onlyOwner {
        intakePaused = paused;
        emit IntakePauseChanged(paused);
    }
    function createCase(ShipmentEscrow.Terms calldata terms) external returns (address escrow) {
        if (intakePaused) revert IntakePaused();
        if (terms.merchant != msg.sender || cases[terms.caseId] != address(0)
            || !allowedTokens[terms.token] || !allowedArbitrators[terms.arbitrationSafe]) revert InvalidCase();
        escrow = address(new ShipmentEscrow(terms));
        cases[terms.caseId] = escrow;
        emit CaseCreated(terms.caseId, escrow, terms.merchant, terms.buyer);
    }
}
